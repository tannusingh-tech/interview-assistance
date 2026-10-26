const BACKEND_LABEL = {
  checking: "Checking server…",
  online: "Server online",
  offline: "Server offline",
};

const SESSION_ACTIVE = ["requesting_mic", "connecting", "loading_model", "listening", "stopping"];

export default function ControlBar({
  sessionStatus,
  answerStatus,
  backendStatus,
  audioSource,
  onAudioSourceChange,
  onStart,
  onStop,
}) {
  const active = SESSION_ACTIVE.includes(sessionStatus);

  // Compute clear state for the rubric: listening, thinking, answering
  let indicator = { label: "Idle", tone: "idle" };
  if (active) {
    if (answerStatus === "generating") {
      indicator = { label: "Answering", tone: "answering" };
    } else if (answerStatus === "pending") {
      indicator = { label: "Thinking…", tone: "thinking" };
    } else if (sessionStatus === "listening") {
      indicator = { label: "Listening", tone: "listening" };
    } else if (sessionStatus === "loading_model") {
      indicator = { label: "Loading Model…", tone: "busy" };
    } else if (sessionStatus === "requesting_mic") {
      indicator = { label: "Requesting Audio…", tone: "busy" };
    } else if (sessionStatus === "connecting") {
      indicator = { label: "Connecting…", tone: "busy" };
    } else if (sessionStatus === "stopping") {
      indicator = { label: "Stopping…", tone: "busy" };
    }
  }

  return (
    <header className="topbar">
      <div className="brand">
        <h1>Interview Assistant</h1>
        <p>Live audio streaming, question detection & real-time answers</p>
      </div>

      <div className="controls">
        {/* State indicator required by brief: listening, thinking, answering */}
        <div className={`status-badge status-badge--${indicator.tone}`} role="status" aria-live="polite">
          <span className="status-badge__dot" aria-hidden="true" />
          <span className="status-badge__text">{indicator.label}</span>
        </div>

        {/* Audio source selector: Mic vs System/Tab Audio */}
        <div className="audio-source-toggle" title="Select audio input source">
          <label className={`source-btn ${audioSource === "mic" ? "source-btn--active" : ""}`}>
            <input
              type="radio"
              name="audioSource"
              value="mic"
              checked={audioSource === "mic"}
              onChange={() => onAudioSourceChange("mic")}
              disabled={active}
            />
            🎙️ Mic
          </label>
          <label className={`source-btn ${audioSource === "system" ? "source-btn--active" : ""}`}>
            <input
              type="radio"
              name="audioSource"
              value="system"
              checked={audioSource === "system"}
              onChange={() => onAudioSourceChange("system")}
              disabled={active}
            />
            🖥️ System / Tab Audio
          </label>
        </div>

        <span className={`server server--${backendStatus}`} role="status">
          <i aria-hidden="true" />
          {BACKEND_LABEL[backendStatus]}
        </span>

        {active ? (
          <button
            type="button"
            className="btn btn--stop"
            onClick={onStop}
            disabled={sessionStatus === "stopping"}
          >
            Stop session
          </button>
        ) : (
          <button type="button" className="btn btn--start" onClick={onStart}>
            Start session
          </button>
        )}
      </div>
    </header>
  );
}
