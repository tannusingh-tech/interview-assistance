/**
 * Question/answer state, driven by backend events. Every answer event carries the id of the
 * question it belongs to; events for any other question are stale and ignored, so a slow
 * answer can never overwrite the one for a newer question.
 *
 * answer.status: idle | pending | generating | done | error | stopped
 */
export const initialQa = {
  question: null,
  answer: { questionId: null, text: "", status: "idle", error: null, latency: null },
};

const ACTIVE = ["pending", "generating"];

export function qaReducer(state, event) {
  if (event.type === "reset") return initialQa;

  if (event.type === "question") {
    if (state.question?.id === event.id) return state; // repeated frame: keep the answer
    return {
      question: { id: event.id, text: event.text },
      answer: { questionId: event.id, text: "", status: "pending", error: null, latency: null },
    };
  }

  if (event.type === "interrupted") {
    if (!ACTIVE.includes(state.answer.status)) return state;
    return { ...state, answer: { ...state.answer, status: "stopped" } };
  }

  const current = state.question?.id;
  if (!current || event.question_id !== current) return state; // stale or unknown question

  const a = state.answer;
  switch (event.type) {
    case "answer_start":
      return a.status === "pending" ? { ...state, answer: { ...a, status: "generating" } } : state;
    case "answer_delta":
      if (!ACTIVE.includes(a.status)) return state; // nothing is appended after done/error
      return { ...state, answer: { ...a, status: "generating", text: a.text + event.text } };
    case "answer_done":
      return ACTIVE.includes(a.status)
        ? { ...state, answer: { ...a, status: "done", latency: event.latency || null } }
        : state;
    case "answer_error":
      return ACTIVE.includes(a.status)
        ? { ...state, answer: { ...a, status: "error", error: event.message || "Answer failed." } }
        : state;
    default:
      return state;
  }
}
