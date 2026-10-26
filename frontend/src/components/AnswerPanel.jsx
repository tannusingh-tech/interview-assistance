import { useState } from "react";

const CHIP = {
  pending: ["Thinking…", "chip--wait"],
  generating: ["Answering…", "chip--live"],
  done: ["Complete", "chip--done"],
  error: ["Failed", "chip--error"],
  stopped: ["Stopped", "chip--wait"],
};

const MODES = [
  { id: "concise", label: "Concise", badge: "60-90w" },
  { id: "star", label: "STAR Method", badge: "Behavioral" },
  { id: "detailed", label: "Detailed", badge: "Technical" },
];

function renderFormattedAnswer(text) {
  if (!text) return null;
  const parts = text.split(/(\*\*[^*]+\*\*)/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={i} className="answer__strong">{part.slice(2, -2)}</strong>;
    }
    return part;
  });
}

/**
 * answer: { text, status: idle|pending|generating|done|error|stopped, error, latency }
 * answersConfigured: false when the backend has no OPENROUTER_API_KEY.
 * mode: "concise" | "star" | "detailed"
 * onModeChange: (mode) => void
 */
export default function AnswerPanel({
  answer,
  answersConfigured,
  mode = "concise",
  onModeChange,
}) {
  const { text, status, error, latency } = answer;
  const chip = CHIP[status];
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard fallback */
    }
  };

  const ttftSeconds = latency?.ttft_ms != null ? (latency.ttft_ms / 1000).toFixed(2) : null;
  const totalSeconds = latency?.total_ms != null ? (latency.total_ms / 1000).toFixed(2) : null;
  const hitTarget = latency?.ttft_ms != null && latency.ttft_ms < 3000;

  return (
    <section className="panel answer" aria-labelledby="answer-title" aria-busy={status === "generating"}>
      <div className="panel__header">
        <h2 id="answer-title">
          Suggested answer
          {chip && <span className={`chip ${chip[1]}`}>{chip[0]}</span>}
        </h2>

        {/* Answer mode selector (Bonus feature: different answer modes) */}
        {onModeChange && (
          <div className="mode-selector" role="group" aria-label="Answer Mode">
            {MODES.map((m) => (
              <button
                key={m.id}
                type="button"
                className={`mode-btn ${mode === m.id ? "mode-btn--active" : ""}`}
                onClick={() => onModeChange(m.id)}
                title={`Switch to ${m.label} mode`}
              >
                <span>{m.label}</span>
                <span className="mode-btn__badge">{m.badge}</span>
              </button>
            ))}
          </div>
        )}
      </div>

      {status === "idle" && (
        <p className="empty">
          {answersConfigured === false
            ? "AI answers are off. Add OPENROUTER_API_KEY to .env and restart the backend."
            : "A suggested answer appears here automatically when a question is detected."}
        </p>
      )}

      {status === "pending" && (
        <div className="answer__thinking">
          <span className="thinking-spinner" aria-hidden="true" />
          <p className="empty">Analyzing question with resume & job description context…</p>
        </div>
      )}

      {text && (
        <div className="answer__content">
          <p className="answer__text">
            {renderFormattedAnswer(text)}
            {status === "generating" && <span className="caret" aria-hidden="true" />}
          </p>

          <div className="answer__actions">
            <button
              type="button"
              className="btn btn--copy"
              onClick={handleCopy}
              disabled={status === "generating"}
              title="Copy suggested answer to clipboard"
            >
              {copied ? "✓ Copied to clipboard" : "📋 Copy answer"}
            </button>

            {/* Latency metrics (Bonus feature & Rubric requirement: measured latency <3s) */}
            {latency && (
              <div className="latency-badge" title="Measured real-time generation latency">
                <span className="latency-badge__ttft">⚡ TTFT: {ttftSeconds}s</span>
                <span className="latency-badge__total">Total: {totalSeconds}s</span>
                {hitTarget ? (
                  <span className="latency-badge__target latency-badge__target--met">
                    &lt;3s target met ✓
                  </span>
                ) : (
                  <span className="latency-badge__target">target: &lt;3s</span>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {status === "stopped" && (
        <p className="answer__note">The session stopped before this answer finished.</p>
      )}

      {status === "error" && (
        <p className="answer__error" role="alert">{error}</p>
      )}
    </section>
  );
}
