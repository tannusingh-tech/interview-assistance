import React, { useEffect, useState } from "react";
import ControlBar from "./components/ControlBar.jsx";
import ContextPanel from "./components/ContextPanel.jsx";
import TranscriptPanel from "./components/TranscriptPanel.jsx";
import ManualQuestionPanel from "./components/ManualQuestionPanel.jsx";
import QuestionPanel from "./components/QuestionPanel.jsx";
import AnswerPanel from "./components/AnswerPanel.jsx";
import { useBackendHealth } from "./hooks/useBackendHealth.js";
import { usePersistentState } from "./hooks/usePersistentState.js";
import { useTranscriptionSession } from "./hooks/useTranscriptionSession.js";

export default function App() {
  const backendStatus = useBackendHealth();
  const [resume, setResume] = usePersistentState("ia.resume", "");
  const [jobDescription, setJobDescription] = usePersistentState("ia.jobDescription", "");
  const [answerMode, setAnswerMode] = usePersistentState("ia.answerMode", "concise");
  const [audioSource, setAudioSource] = useState("mic");

  const [manualState, setManualState] = useState({
    active: false,
    question: "",
    text: "",
    status: "idle",
    error: null,
    latency: null,
  });

  const session = useTranscriptionSession({
    resume,
    jobDescription,
    mode: answerMode,
    audioSource,
  });

  // When a live question arrives, switch active focus to the live session
  useEffect(() => {
    if (session.question?.id) {
      setManualState((prev) => ({ ...prev, active: false }));
    }
  }, [session.question?.id]);

  const handleStartManualQuestion = (qText) => {
    setManualState({
      active: true,
      question: qText,
      text: "",
      status: "pending",
      error: null,
      latency: null,
    });
  };

  const handleManualAnswer = ({ questionText, text, latency }) => {
    setManualState({
      active: true,
      question: questionText,
      text,
      status: "done",
      error: null,
      latency,
    });
  };

  const handleManualError = (errorMsg) => {
    setManualState((prev) => ({
      ...prev,
      status: "error",
      error: errorMsg,
    }));
  };

  // Determine current active display for Question and Answer
  const showManual = manualState.active && manualState.question;
  const currentQuestion = showManual ? manualState.question : session.question?.text;
  const currentAnswer = showManual
    ? {
        questionId: "manual",
        text: manualState.text,
        status: manualState.status,
        error: manualState.error,
        latency: manualState.latency,
      }
    : session.answer;

  return (
    <div className="app">
      <ControlBar
        sessionStatus={session.status}
        answerStatus={currentAnswer.status}
        backendStatus={backendStatus}
        audioSource={audioSource}
        onAudioSourceChange={setAudioSource}
        onStart={session.start}
        onStop={session.stop}
      />

      <p className="phase-note">
        <strong>POC Pipeline:</strong> Audio Capture ({audioSource === "mic" ? "Microphone" : "System/Tab Audio"}) → Local Whisper STT → Question Detection → Streaming OpenRouter LLM. Target latency from question end to first token: &lt;3s.
      </p>

      {session.error && (
        <div className="banner banner--error" role="alert">
          <span>{session.error}</span>
          <button type="button" onClick={session.dismissError}>Dismiss</button>
        </div>
      )}
      {session.notice && (
        <div className="banner" role="status">
          <span>{session.notice}</span>
          <button type="button" onClick={session.dismissNotice}>Dismiss</button>
        </div>
      )}

      <details className="context-details" defaultOpen={false}>
        <summary>Interview Context (Resume & Job Description)</summary>
        <ContextPanel
          resume={resume}
          jobDescription={jobDescription}
          onResume={setResume}
          onJobDescription={setJobDescription}
        />
      </details>

      <ManualQuestionPanel
        resume={resume}
        jobDescription={jobDescription}
        mode={answerMode}
        onStartManualQuestion={handleStartManualQuestion}
        onManualAnswer={handleManualAnswer}
        onManualError={handleManualError}
      />

      <main className="layout">
        <div className="stack">
          <QuestionPanel question={currentQuestion} />
          <AnswerPanel
            answer={currentAnswer}
            answersConfigured={session.answersConfigured}
            mode={answerMode}
            onModeChange={setAnswerMode}
          />
        </div>

        <TranscriptPanel
          segments={session.segments}
          interim={session.interim}
          status={session.status}
        />
      </main>
    </div>
  );
}
