"""WebSocket endpoint for chunked speech-to-text.

Protocol (client -> server):
  binary frame : one COMPLETE audio file (WebM/Ogg/MP4/WAV), never a fragment
  text frame   : {"type": "stop"} to flush pending audio and end the session
                 {"type": "ping"}  -> {"type": "pong"}
                 {"type": "context", "resume": str, "job_description": str}
                     candidate background used to tailor answers (resend whenever it changes)

Server -> client JSON frames:
  {"type":"status","state":"loading_model"}      model is loading / downloading
  {"type":"ready", ...model info}                client may start sending audio
  {"type":"processing","pending":n}              a chunk is being transcribed
  {"type":"transcript","seq":n,"segments":[{"id","text"}]}   confirmed text
  {"type":"idle"}                                queue drained
  {"type":"warning"|"error","code","message","fatal"}
  {"type":"stopped"}                             flush finished after "stop"

Phase 3 frames (additive; the frames above are unchanged). ids tie every answer frame to its question:
  {"type":"question","id","text"}                a complete interview question was detected
  {"type":"answer_start","question_id"}
  {"type":"answer_delta","question_id","text"}   streamed answer text
  {"type":"answer_done","question_id"}
  {"type":"answer_error","question_id","code","message"}
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket

from app.services.answer_session import AnswerCoordinator
from app.services.pipeline import ChunkPipeline
from app.services.question_detector import Question, QuestionDetector
from app.services.transcription import TranscriptionError, sniff_container

router = APIRouter()
logger = logging.getLogger("interview_assistant.ws")


class TranscriptionSession:
    def __init__(self, ws: WebSocket, transcriber, settings, answer_client=None):
        self.ws = ws
        self.transcriber = transcriber
        self.settings = settings
        self.pipeline = ChunkPipeline(transcriber, settings.audio_overlap_seconds)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.closed = False
        self._seq = 0
        self._pending = 0  # real chunks queued or in flight (excludes the stop sentinel)
        self._stopping = False
        self._send_lock = asyncio.Lock()  # transcript and answer tasks share one socket
        self.detector = QuestionDetector(settings.question_dedupe_seconds)
        self.answer_client = answer_client
        self.answers = AnswerCoordinator(
            answer_client, self.send, settings.answer_max_context_chars,
            settings.openrouter_use_system_role,
        )

    async def send(self, payload: dict) -> bool:
        if self.closed:
            return False
        try:
            async with self._send_lock:
                await self.ws.send_json(payload)
            return True
        except Exception:  # client already gone
            self.closed = True
            return False

    async def notify(self, kind: str, code: str, message: str, fatal: bool = False) -> None:
        await self.send({"type": kind, "code": code, "message": message, "fatal": fatal})

    async def run(self) -> None:
        worker: asyncio.Task | None = None
        try:
            await self.send({"type": "status", "state": "loading_model"})
            try:
                await asyncio.to_thread(self.transcriber.ensure_loaded)
            except TranscriptionError as exc:
                await self.notify("error", exc.code, exc.message, fatal=True)
                return
            await self.send({
                "type": "ready",
                **self.transcriber.describe(),
                "answers_configured": bool(getattr(self.answer_client, "configured", False)),
                "answers_model": getattr(self.answer_client, "model", ""),
            })
            logger.info("Session ready")

            worker = asyncio.create_task(self._worker())
            graceful = await self._receive_loop()
            if graceful:
                self._stopping = True  # finish queued transcription, but start no new answers
                self.queue.put_nowait(None)
                try:
                    await asyncio.wait_for(worker, timeout=self.settings.stop_flush_timeout_seconds)
                except asyncio.TimeoutError:
                    logger.warning("Timed out flushing pending audio on stop")
                await self.answers.cancel()
                await self.send({"type": "stopped"})
        except Exception:
            logger.exception("Unexpected session error")
            await self.notify("error", "internal_error", "Unexpected server error.", fatal=True)
        finally:
            if worker is not None and not worker.done():
                worker.cancel()
            if worker is not None:
                await asyncio.gather(worker, return_exceptions=True)
            await self.answers.cancel()  # abort any in-flight LLM request on disconnect/error
            await self._close()
            logger.info("Session closed")

    async def _close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                await self.ws.close()
            except Exception:
                pass

    async def _receive_loop(self) -> bool:
        """Return True on a graceful stop request, False if the client disconnected."""
        while True:
            msg = await self.ws.receive()
            if msg["type"] == "websocket.disconnect":
                self.closed = True
                logger.info("Client disconnected")
                return False
            if msg.get("bytes") is not None:
                await self._on_audio(msg["bytes"])
            elif msg.get("text") is not None:
                try:
                    obj = json.loads(msg["text"])
                    kind = obj.get("type") if isinstance(obj, dict) else None
                except ValueError:
                    kind = None
                if kind == "stop":
                    return True
                if kind == "context":
                    self._on_context(obj)
                elif kind == "ping":
                    await self.send({"type": "pong"})
                else:
                    await self.notify("warning", "bad_message", "Unrecognised control message.")

    def _on_context(self, obj: dict) -> None:
        def field(name: str) -> str:
            value = obj.get(name)
            return value[:50_000] if isinstance(value, str) else ""

        mode = obj.get("mode") if isinstance(obj.get("mode"), str) else "concise"
        self.answers.set_context(field("resume"), field("job_description"), mode=mode)

    async def _on_question(self, question: Question) -> None:
        logger.info("Question detected: id=%s words=%d", question.id, len(question.text.split()))
        await self.send({"type": "question", "id": question.id, "text": question.text})
        await self.answers.submit(question, self.detector.recent_transcript())

    async def _detect_questions(self, pieces: list[str]) -> None:
        try:
            # No new text from a chunk means the speaker paused: that completes a held question.
            found = self.detector.feed(" ".join(pieces)) if pieces else self.detector.on_silence()
            for question in found:
                await self._on_question(question)
        except Exception:
            logger.exception("Question detection failed")

    async def _on_audio(self, data: bytes) -> None:
        if not data or sniff_container(data) is None:
            await self.notify("warning", "invalid_audio", "Skipped a chunk that is not a complete audio file.")
            return
        if len(data) > self.settings.max_chunk_bytes:
            await self.notify("warning", "chunk_too_large", "Skipped an oversized audio chunk.")
            return
        if self.queue.qsize() >= self.settings.max_pending_chunks:
            try:
                self.queue.get_nowait()  # drop the oldest to keep latency bounded
                self._pending -= 1
            except asyncio.QueueEmpty:
                pass
            await self.notify("warning", "backlog_dropped",
                              "Transcription is falling behind; dropped the oldest audio chunk. "
                              "Try a smaller WHISPER_MODEL.")
        self._seq += 1
        self._pending += 1
        self.queue.put_nowait((self._seq, data))

    async def _worker(self) -> None:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            seq, data = item
            await self.send({"type": "processing", "pending": self._pending})
            try:
                pieces = await asyncio.to_thread(self.pipeline.process, data)
            except TranscriptionError as exc:
                await self.notify("error", exc.code, exc.message)
            except Exception:
                logger.exception("Unexpected failure processing chunk %d", seq)
                await self.notify("error", "transcription_failed", "Transcription failed on this chunk.")
            else:
                if pieces:
                    await self.send({
                        "type": "transcript",
                        "seq": seq,
                        "segments": [{"id": f"{seq}-{i}", "text": t} for i, t in enumerate(pieces)],
                    })
                if not self._stopping:
                    await self._detect_questions(pieces)
            self._pending -= 1
            if self._pending == 0:
                await self.send({"type": "idle"})


@router.websocket("/ws/transcribe")
async def transcribe_ws(ws: WebSocket) -> None:
    settings = ws.app.state.settings
    origin = ws.headers.get("origin")
    # CORS middleware does not cover WebSockets, so check the Origin explicitly.
    if origin is not None and origin not in settings.cors_origin_list:
        logger.warning("Rejected WebSocket from origin %s", origin)
        await ws.close(code=1008)
        return
    await ws.accept()
    await TranscriptionSession(
        ws, ws.app.state.transcriber, settings, ws.app.state.answer_client
    ).run()
