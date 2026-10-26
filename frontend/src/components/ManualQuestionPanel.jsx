import { useState } from "react";
import { API_BASE_URL } from "../api.js";

const SAMPLE_QUESTIONS = [
  "Tell me about yourself and your background.",
  "Describe a difficult technical bug you solved.",
  "How do you design scalable backend architectures?",
];

export default function ManualQuestionPanel({
  resume,
  jobDescription,
  mode = "concise",
  onStartManualQuestion,
  onManualAnswer,
  onManualError,
}) {
  const [question, setQuestion] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const handleSubmit = async () => {
    const qText = question.trim();
    if (!qText) return;
    setLoading(true);
    setError(null);
    onStartManualQuestion?.(qText);

    try {
      const response = await fetch(`${API_BASE_URL}/manual_question`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: qText,
          resume: resume || "",
          job_description: jobDescription || "",
          mode: mode || "concise",
        }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.detail || `Request failed (${response.status})`);
      }
      const data = await response.json();
      onManualAnswer?.({
        questionText: qText,
        text: data.answer,
        latency: data.latency,
      });
    } catch (e) {
      console.error(e);
      const msg = e.message || "Failed to generate answer.";
      setError(msg);
      onManualError?.(msg);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section className="panel manual-question" aria-labelledby="manual-question-title">
      <div className="manual-question__header">
        <h2 id="manual-question-title">Manual question test</h2>
        <span className="hint">Type, paste, or pick a sample question to test without a live mic:</span>
      </div>

      <div className="sample-chips">
        {SAMPLE_QUESTIONS.map((sq, i) => (
          <button
            key={i}
            type="button"
            className="chip-sample"
            onClick={() => setQuestion(sq)}
            disabled={loading}
          >
            {sq}
          </button>
        ))}
      </div>

      <div className="manual-question__input-row">
        <textarea
          className="question-input"
          placeholder="Type or paste an interview question…"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          rows={2}
          disabled={loading}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              handleSubmit();
            }
          }}
        />
        <button
          type="button"
          className="btn btn--generate"
          onClick={handleSubmit}
          disabled={loading || !question.trim()}
        >
          {loading ? "Generating…" : "Generate answer"}
        </button>
      </div>

      {error && (
        <p className="answer__error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}
