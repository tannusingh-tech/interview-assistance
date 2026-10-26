import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { API_BASE_URL } from "../api.js";
import { ChunkedRecorder } from "../audio/ChunkedRecorder.js";
import { TranscriptionSocket } from "../audio/TranscriptionSocket.js";
import { mapMicError, mergeSegments, pickMimeType, toWsUrl } from "../audio/utils.js";
import { initialQa, qaReducer } from "../session/qaState.js";

const CHUNK_MS = Number(import.meta.env.VITE_AUDIO_CHUNK_MS) || 2500;

function stopTracks(stream) {
  stream?.getTracks().forEach((t) => {
    t.onended = null;
    t.stop();
  });
}

/**
 * Owns the whole microphone / system audio -> WebSocket -> transcript lifecycle.
 * status: idle | requesting_mic | connecting | loading_model | listening | stopping | error
 *
 * It also receives the detected question and the streamed answer on the same WebSocket and
 * sends the Resume / Job description / Answer Mode to the backend.
 */
export function useTranscriptionSession({
  resume = "",
  jobDescription = "",
  mode = "concise",
  audioSource = "mic",
} = {}) {
  const [status, setStatus] = useState("idle");
  const [segments, setSegments] = useState([]);
  const [interim, setInterim] = useState("");
  const [error, setError] = useState(null);
  const [notice, setNotice] = useState(null);
  const [qa, dispatchQa] = useReducer(qaReducer, initialQa);
  const [answersConfigured, setAnswersConfigured] = useState(null); // null = unknown until connected
  const ref = useRef({ token: 0, active: false, stream: null, recorder: null, socket: null });
  const contextRef = useRef({ resume, jobDescription, mode, audioSource });
  contextRef.current = { resume, jobDescription, mode, audioSource };

  const sendContext = useCallback((socket) => {
    const { resume: r, jobDescription: j, mode: m } = contextRef.current;
    socket.sendJson({ type: "context", resume: r, job_description: j, mode: m });
  }, []);

  /** Tear everything down immediately, discarding unsent audio. */
  const release = useCallback(() => {
    const r = ref.current;
    r.token += 1;
    r.active = false;
    const { recorder, socket, stream } = r;
    r.recorder = r.socket = r.stream = null;
    recorder?.abort();
    stopTracks(stream);
    if (socket) socket.dead = true;
    socket?.close();
    setInterim("");
    dispatchQa({ type: "interrupted" });
  }, []);

  const failSession = useCallback(
    (message) => {
      release();
      setError(message);
      setStatus("error");
    },
    [release]
  );

  const start = useCallback(async () => {
    const r = ref.current;
    if (r.active) return;
    r.active = true;
    const token = (r.token += 1);
    const stale = () => r.token !== token;

    setError(null);
    setNotice(null);
    setSegments([]);
    setInterim("");
    dispatchQa({ type: "reset" });

    const mimeType = pickMimeType();
    if (!navigator.mediaDevices?.getUserMedia || mimeType === null) {
      failSession(
        "This browser cannot record audio. Use a current Chrome, Edge, Firefox or Safari, and open the app at http://localhost:5173."
      );
      return;
    }

    setStatus("requesting_mic");
    let stream;
    try {
      if (contextRef.current.audioSource === "system") {
        if (!navigator.mediaDevices?.getDisplayMedia) {
          throw new Error("Tab/System audio capture is not supported in this browser. Please use Microphone.");
        }
        stream = await navigator.mediaDevices.getDisplayMedia({
          video: true,
          audio: true,
        });
        if (stream.getAudioTracks().length === 0) {
          stopTracks(stream);
          throw new Error("No audio track detected. In the browser share dialog, make sure to check 'Also share tab audio' or select a tab with audio.");
        }
        // Stop unused video tracks immediately to keep performance optimal
        stream.getVideoTracks().forEach((t) => t.stop());
      } else {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
        });
      }
    } catch (err) {
      if (!stale()) failSession(mapMicError(err));
      return;
    }
    if (stale()) {
      stopTracks(stream); // Stop was pressed while the permission prompt was open
      return;
    }
    r.stream = stream;
    stream.getAudioTracks().forEach((t) => {
      t.onended = () => {
        if (!stale()) failSession("The audio source was disconnected. Reconnect it and press Start session.");
      };
    });

    setStatus("connecting");
    const socket = new TranscriptionSocket(toWsUrl(API_BASE_URL), {
      onMessage: (msg) => {
        if (socket.dead) return; // late frames after teardown are ignored
        switch (msg.type) {
          case "status":
            if (msg.state === "loading_model") setStatus("loading_model");
            break;
          case "processing":
            setInterim("Transcribing…");
            break;
          case "transcript":
            setSegments((prev) =>
              mergeSegments(prev, msg.segments.map((s) => ({ id: `${token}:${s.id}`, text: s.text })))
            );
            break;
          case "idle":
            setInterim("");
            break;
          case "question":
          case "answer_start":
          case "answer_delta":
          case "answer_done":
          case "answer_error":
            dispatchQa(msg); // reducer keys everything by question id and drops stale events
            break;
          case "warning":
            setNotice(msg.message);
            break;
          case "error":
            if (msg.fatal) failSession(msg.message);
            else setNotice(msg.message);
            break;
          default:
            break;
        }
      },
      onClose: () => {
        if (!socket.dead && ref.current.socket === socket && ref.current.active) {
          failSession("Lost the connection to the backend. Make sure it is running, then press Start session.");
        }
      },
    });
    r.socket = socket;

    let ready;
    try {
      ready = await socket.open();
    } catch (err) {
      if (!stale()) failSession(err.message);
      return;
    }
    if (stale()) return;
    setAnswersConfigured(ready.answers_configured !== false);
    sendContext(socket);

    const recorder = new ChunkedRecorder(stream, {
      chunkMs: CHUNK_MS,
      mimeType,
      onChunk: (blob) => socket.sendChunk(blob),
      onError: () => failSession("Audio recording failed. Press Start session to try again."),
    });
    r.recorder = recorder;
    recorder.start();
    setStatus("listening");
  }, [failSession, sendContext]);

  const stop = useCallback(async () => {
    const r = ref.current;
    if (!r.active) return;
    r.token += 1; // invalidates any start() still in flight
    r.active = false;
    const { recorder, socket, stream } = r;
    r.recorder = r.socket = r.stream = null;

    setStatus(recorder ? "stopping" : "idle");
    try {
      await recorder?.stop(); // emits the final chunk
    } catch {
      /* fall through to cleanup */
    }
    stopTracks(stream); // release the microphone as soon as recording ends
    if (recorder && socket) {
      try {
        await socket.finish(); // server transcribes remaining audio, then confirms
      } catch {
        /* ignore */
      }
    }
    if (socket) socket.dead = true;
    socket?.close();
    setInterim("");
    dispatchQa({ type: "interrupted" }); // an unfinished answer is cancelled by the backend on Stop
    setStatus((s) => (s === "error" ? s : "idle"));
  }, []);

  // Re-send the background and answer mode when the user edits them mid-session (debounced).
  useEffect(() => {
    if (status !== "listening") return undefined;
    const timer = setTimeout(() => {
      const socket = ref.current.socket;
      if (socket && !socket.dead) sendContext(socket);
    }, 600);
    return () => clearTimeout(timer);
  }, [resume, jobDescription, mode, status, sendContext]);

  // Release everything if the page/component goes away mid-session.
  useEffect(() => () => release(), [release]);

  return {
    status, segments, interim, error, notice, start, stop,
    question: qa.question, answer: qa.answer, answersConfigured,
    dismissError: () => setError(null), dismissNotice: () => setNotice(null) };
}
