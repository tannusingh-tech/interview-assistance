import threading
import time

import numpy as np
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.answers import AnswerError
from app.services.transcription import SAMPLE_RATE, Segment
from tests.helpers import FAKE_WEBM


class ScriptedTranscriber:
    """One scripted transcript text per chunk ("" = silence)."""

    loaded = False

    def __init__(self, texts):
        self.texts = list(texts)

    def describe(self):
        return {"engine": "fake", "model": "fake", "device": "cpu", "compute_type": "int8"}

    def ensure_loaded(self):
        self.loaded = True

    def decode(self, data):
        return np.zeros(SAMPLE_RATE * 2, dtype=np.float32)

    def transcribe_audio(self, audio):
        text = self.texts.pop(0) if self.texts else ""
        return [Segment(text, 0.0, 1.5)] if text else []


class FakeLLM:
    configured = True
    model = "fake/model"

    def __init__(self, answers=(("Answer ", "one."), ("Answer ", "two.")), hold_calls=(), error=None):
        self.answers, self.hold_calls, self.error = list(answers), set(hold_calls), error
        self.calls, self.cancelled, self.gate = [], [], threading.Event()

    async def stream(self, messages):
        import asyncio

        index = len(self.calls)
        self.calls.append(messages)
        try:
            if self.error:
                raise self.error
            for i, d in enumerate(self.answers[min(index, len(self.answers) - 1)]):
                yield d
                if i == 0 and index in self.hold_calls:
                    while not self.gate.is_set():
                        await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled.append(index)
            raise


def make_client(texts, llm, **overrides):
    settings = Settings(_env_file=None, audio_overlap_seconds=0, **overrides)
    app = create_app(settings, transcriber=ScriptedTranscriber(texts), answer_client=llm)
    return TestClient(app)


def drain_until(ws, kind, limit=40, **match):
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == kind and all(msg.get(k) == v for k, v in match.items()):
            return msg, seen
    raise AssertionError(f"never saw {kind!r} {match}; got {seen}")


def types(seen):
    return [m["type"] for m in seen]


def test_ready_reports_answer_configuration():
    llm = FakeLLM()
    with make_client([], llm).websocket_connect("/ws/transcribe") as ws:
        ready, _ = drain_until(ws, "ready")
        assert ready["answers_configured"] is True and ready["answers_model"] == "fake/model"


def test_question_is_detected_and_answer_streams_with_matching_ids_and_context():
    llm = FakeLLM()
    with make_client(["How would you design a rate limiter?"], llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_json({"type": "context", "resume": "RESUME: built APIs at Acme", "job_description": "JD: Go engineer"})
        ws.send_bytes(FAKE_WEBM)
        q, seen = drain_until(ws, "question")
        assert q["id"] == "q1" and q["text"] == "How would you design a rate limiter?"
        done, seen2 = drain_until(ws, "answer_done")
        stream = seen + seen2
    assert types(stream).index("transcript") < types(stream).index("question") < types(stream).index("answer_start")
    deltas = [m for m in stream if m["type"] == "answer_delta"]
    assert "".join(m["text"] for m in deltas) == "Answer one."
    assert {m["question_id"] for m in deltas} == {"q1"} and done["question_id"] == "q1"
    prompt = llm.calls[0][1]["content"]
    assert "RESUME: built APIs at Acme" in prompt and "JD: Go engineer" in prompt
    assert prompt.rstrip().endswith("How would you design a rate limiter?")


def test_question_split_across_chunks_triggers_one_request():
    llm = FakeLLM()
    with make_client(["Can you explain how", "RAG works?"], llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM)
        ws.send_bytes(FAKE_WEBM)
        q, _ = drain_until(ws, "question")
        drain_until(ws, "answer_done")
        ws.send_json({"type": "stop"})
        _, rest = drain_until(ws, "stopped")
    assert q["text"] == "Can you explain how RAG works?"
    assert "question" not in types(rest) and len(llm.calls) == 1


def test_question_without_question_mark_is_submitted_once_the_speaker_pauses():
    llm = FakeLLM()
    with make_client(["Tell me about yourself", ""], llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM)
        ws.send_bytes(FAKE_WEBM)  # silent chunk
        q, _ = drain_until(ws, "question")
        drain_until(ws, "answer_done")
    assert q["text"] == "Tell me about yourself" and len(llm.calls) == 1


def test_repeated_transcript_from_overlapping_audio_does_not_resubmit():
    llm = FakeLLM()
    texts = ["Tell me about yourself.", "Tell me about yourself.", ""]
    with make_client(texts, llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        for _ in texts:
            ws.send_bytes(FAKE_WEBM)
        ws.send_json({"type": "stop"})
        _, seen = drain_until(ws, "stopped")
    assert len(llm.calls) == 1


def test_non_questions_never_call_the_llm():
    llm = FakeLLM()
    with make_client(["I worked on a payments platform.", "Thanks for joining."], llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM)
        ws.send_bytes(FAKE_WEBM)
        ws.send_json({"type": "stop"})
        _, seen = drain_until(ws, "stopped")
    assert llm.calls == [] and "question" not in types(seen)


def test_transcription_keeps_flowing_while_an_answer_streams():
    llm = FakeLLM(hold_calls={0})
    with make_client(["What is a hash map?", "Sure, go on."], llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM)
        drain_until(ws, "answer_delta")  # answer is now stuck mid-stream
        ws.send_bytes(FAKE_WEBM)
        _, seen = drain_until(ws, "transcript", seq=2)
        assert "answer_done" not in types(seen)  # transcript arrived while generation was still open
        llm.gate.set()
        done, _ = drain_until(ws, "answer_done")
    assert done["question_id"] == "q1"


def test_newer_question_supersedes_and_cancels_the_older_answer():
    llm = FakeLLM(hold_calls={0})
    with make_client(["Tell me about yourself.", "What is a hash map?"], llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM)
        drain_until(ws, "answer_delta", question_id="q1")
        ws.send_bytes(FAKE_WEBM)
        q2, seen = drain_until(ws, "question")
        done, seen2 = drain_until(ws, "answer_done")
    assert q2["id"] == "q2" and done["question_id"] == "q2"
    assert llm.cancelled == [0]
    assert not any(m.get("question_id") == "q1" and m["type"] in ("answer_done", "answer_delta")
                   for m in seen + seen2)


def test_provider_error_is_reported_for_its_question_and_session_continues():
    llm = FakeLLM(error=AnswerError("rate_limited", "OpenRouter rate limit reached."))
    with make_client(["What is a hash map?", "I see."], llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM)
        err, _ = drain_until(ws, "answer_error")
        assert err["question_id"] == "q1" and err["code"] == "rate_limited"
        ws.send_bytes(FAKE_WEBM)
        assert drain_until(ws, "transcript", seq=2)[0]["segments"][0]["text"] == "I see."


def test_missing_api_key_reports_not_configured_without_breaking_transcription():
    settings = Settings(_env_file=None, audio_overlap_seconds=0, openrouter_api_key="")
    app = create_app(settings, transcriber=ScriptedTranscriber(["What is a hash map?"]))
    with TestClient(app).websocket_connect("/ws/transcribe") as ws:
        ready, _ = drain_until(ws, "ready")
        assert ready["answers_configured"] is False
        ws.send_bytes(FAKE_WEBM)
        drain_until(ws, "question")
        err, _ = drain_until(ws, "answer_error")
    assert err["code"] == "not_configured" and "OPENROUTER_API_KEY" in err["message"]


def test_stop_cancels_in_flight_answer_and_does_not_flush_new_questions():
    llm = FakeLLM(hold_calls={0})
    with make_client(["What is a hash map?", "Can you explain how"], llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM)
        drain_until(ws, "answer_delta")
        ws.send_bytes(FAKE_WEBM)
        ws.send_json({"type": "stop"})
        _, seen = drain_until(ws, "stopped")
    assert llm.cancelled == [0]
    assert "answer_done" not in types(seen) and types(seen).count("question") <= 0


def test_disconnect_aborts_the_in_flight_llm_request():
    llm = FakeLLM(hold_calls={0})
    with make_client(["What is a hash map?"], llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM)
        drain_until(ws, "answer_delta")
    deadline = time.time() + 5
    while not llm.cancelled and time.time() < deadline:
        time.sleep(0.05)
    assert llm.cancelled == [0]


def test_malformed_context_message_is_ignored_safely():
    llm = FakeLLM()
    with make_client(["What is a hash map?"], llm).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_json({"type": "context", "resume": 123, "job_description": None})
        ws.send_bytes(FAKE_WEBM)
        drain_until(ws, "answer_done")
    assert "(not provided)" in llm.calls[0][1]["content"]
