import { describe, expect, it } from "vitest";
import { initialQa, qaReducer } from "./qaState.js";

const run = (events, start = initialQa) => events.reduce(qaReducer, start);
const q = (id, text = "Why?") => ({ type: "question", id, text });

describe("qaReducer", () => {
  it("shows the question immediately and waits for the answer", () => {
    const s = run([q("q1", "Tell me about yourself.")]);
    expect(s.question).toEqual({ id: "q1", text: "Tell me about yourself." });
    expect(s.answer).toMatchObject({ questionId: "q1", status: "pending", text: "" });
  });

  it("streams deltas progressively and completes", () => {
    let s = run([q("q1"), { type: "answer_start", question_id: "q1" }]);
    expect(s.answer.status).toBe("generating");
    s = run([{ type: "answer_delta", question_id: "q1", text: "I am " }], s);
    expect(s.answer.text).toBe("I am ");
    s = run([{ type: "answer_delta", question_id: "q1", text: "an engineer." }, { type: "answer_done", question_id: "q1" }], s);
    expect(s.answer).toMatchObject({ text: "I am an engineer.", status: "done" });
  });

  it("ignores events for a stale question so they cannot overwrite the newer answer", () => {
    let s = run([q("q1"), { type: "answer_start", question_id: "q1" }, { type: "answer_delta", question_id: "q1", text: "old" }]);
    s = run([q("q2", "New question?")], s);
    s = run([
      { type: "answer_delta", question_id: "q1", text: " LATE" },
      { type: "answer_done", question_id: "q1" },
      { type: "answer_error", question_id: "q1", message: "late failure" },
    ], s);
    expect(s.question.id).toBe("q2");
    expect(s.answer).toMatchObject({ questionId: "q2", text: "", status: "pending", error: null });
  });

  it("ignores answer events before any question exists", () => {
    expect(run([{ type: "answer_delta", question_id: "q9", text: "x" }])).toBe(initialQa);
  });

  it("does not reset the answer when the same question frame is repeated", () => {
    const s = run([q("q1"), { type: "answer_start", question_id: "q1" }, { type: "answer_delta", question_id: "q1", text: "hi" }, q("q1")]);
    expect(s.answer.text).toBe("hi");
  });

  it("keeps partial text and shows the message on error; later deltas are ignored", () => {
    const s = run([
      q("q1"),
      { type: "answer_start", question_id: "q1" },
      { type: "answer_delta", question_id: "q1", text: "partial" },
      { type: "answer_error", question_id: "q1", message: "Rate limit reached" },
      { type: "answer_delta", question_id: "q1", text: " more" },
    ]);
    expect(s.answer).toMatchObject({ status: "error", text: "partial", error: "Rate limit reached" });
  });

  it("an error with no text is reported while pending (e.g. missing API key)", () => {
    const s = run([q("q1"), { type: "answer_error", question_id: "q1", message: "AI answers are off" }]);
    expect(s.answer).toMatchObject({ status: "error", text: "", error: "AI answers are off" });
  });

  it("marks only an unfinished answer as stopped", () => {
    const mid = run([q("q1"), { type: "answer_start", question_id: "q1" }, { type: "answer_delta", question_id: "q1", text: "abc" }, { type: "interrupted" }]);
    expect(mid.answer).toMatchObject({ status: "stopped", text: "abc" });
    const done = run([q("q1"), { type: "answer_done", question_id: "q1" }, { type: "interrupted" }]);
    expect(done.answer.status).toBe("done");
  });

  it("records latency metrics when answer_done arrives", () => {
    const s = run([
      q("q1"),
      { type: "answer_start", question_id: "q1" },
      { type: "answer_delta", question_id: "q1", text: "Answer." },
      { type: "answer_done", question_id: "q1", latency: { ttft_ms: 850, total_ms: 2100 } },
    ]);
    expect(s.answer.latency).toEqual({ ttft_ms: 850, total_ms: 2100 });
  });

  it("reset clears everything", () => {
    expect(run([q("q1"), { type: "reset" }])).toBe(initialQa);
  });
});
