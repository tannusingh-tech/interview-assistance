import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from app.services.transcription import SAMPLE_RATE, Segment, TranscriptionError
from tests.helpers import FAKE_WEBM


class FakeTranscriber:
    """Implements the transcriber interface without any model or decoding."""

    def __init__(self, load_error=None, gate: threading.Event | None = None):
        self.load_error, self.gate = load_error, gate
        self.loaded = False
        self.n = 0
        self.fail_on: set[int] = set()
        self.processed = 0

    def describe(self):
        return {"engine": "fake", "model": "fake", "device": "cpu", "compute_type": "int8"}

    def ensure_loaded(self):
        if self.load_error:
            raise self.load_error
        self.loaded = True

    def decode(self, data):
        return np.zeros(SAMPLE_RATE * 2, dtype=np.float32)

    def transcribe_audio(self, audio):
        if self.gate is not None:
            self.gate.wait(timeout=5)
        self.n += 1
        if self.n in self.fail_on:
            raise TranscriptionError("transcription_failed", "boom")
        self.processed += 1
        return [Segment(f"chunk number {self.n}", 0.0, 1.5)]


def make_client(transcriber, **overrides):
    settings = Settings(_env_file=None, audio_overlap_seconds=0, **overrides)
    return TestClient(create_app(settings, transcriber=transcriber))


def drain_until(ws, kind, limit=20):
    seen = []
    for _ in range(limit):
        msg = ws.receive_json()
        seen.append(msg)
        if msg["type"] == kind:
            return msg, seen
    raise AssertionError(f"never saw {kind!r}; got {seen}")


def connect_ready(client):
    ws = client.websocket_connect("/ws/transcribe")
    sock = ws.__enter__()
    assert sock.receive_json() == {"type": "status", "state": "loading_model"}
    ready, _ = drain_until(sock, "ready")
    return ws, sock, ready


def test_handshake_loads_model_then_reports_ready():
    t = FakeTranscriber()
    with make_client(t).websocket_connect("/ws/transcribe") as ws:
        assert ws.receive_json()["state"] == "loading_model"
        ready = ws.receive_json()
        assert ready["type"] == "ready" and ready["engine"] == "fake"
        assert t.loaded
        ws.send_json({"type": "stop"})
        assert drain_until(ws, "stopped")[0]["type"] == "stopped"


def test_audio_chunk_returns_confirmed_transcript_with_unique_ids():
    with make_client(FakeTranscriber()).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM)
        ws.send_bytes(FAKE_WEBM)
        ws.send_json({"type": "stop"})
        _, seen = drain_until(ws, "stopped")
    transcripts = [m for m in seen if m["type"] == "transcript"]
    ids = [s["id"] for m in transcripts for s in m["segments"]]
    assert [m["seq"] for m in transcripts] == [1, 2]
    assert ids == ["1-0", "2-0"] and len(set(ids)) == 2
    assert any(m["type"] == "processing" for m in seen)
    assert any(m["type"] == "idle" for m in seen)


def test_stop_flushes_pending_audio_before_stopped():
    t = FakeTranscriber()
    with make_client(t).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        for _ in range(3):
            ws.send_bytes(FAKE_WEBM)
        ws.send_json({"type": "stop"})
        _, seen = drain_until(ws, "stopped")
    assert t.processed == 3
    assert sum(1 for m in seen if m["type"] == "transcript") == 3


def test_malformed_audio_is_rejected_but_session_continues():
    with make_client(FakeTranscriber()).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(b"this is not an audio container")
        warn, _ = drain_until(ws, "warning")
        assert warn["code"] == "invalid_audio" and warn["fatal"] is False
        ws.send_bytes(FAKE_WEBM)
        assert drain_until(ws, "transcript")[0]["segments"][0]["text"] == "chunk number 1"


def test_oversized_chunk_is_rejected():
    with make_client(FakeTranscriber(), max_chunk_bytes=100).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM + b"\x00" * 200)
        assert drain_until(ws, "warning")[0]["code"] == "chunk_too_large"


def test_transcription_error_is_reported_and_next_chunk_still_works():
    t = FakeTranscriber()
    t.fail_on = {1}
    with make_client(t).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_bytes(FAKE_WEBM)
        err, _ = drain_until(ws, "error")
        assert err["code"] == "transcription_failed" and err["fatal"] is False
        ws.send_bytes(FAKE_WEBM)
        assert drain_until(ws, "transcript")[0]["seq"] == 2


def test_model_load_failure_is_fatal_and_closes():
    t = FakeTranscriber(load_error=TranscriptionError("model_load_failed", "cannot load"))
    with make_client(t).websocket_connect("/ws/transcribe") as ws:
        err, _ = drain_until(ws, "error")
        assert err["code"] == "model_load_failed" and err["fatal"] is True
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_disallowed_origin_is_rejected():
    client = make_client(FakeTranscriber(), cors_origins="http://localhost:5173")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/transcribe", headers={"origin": "http://evil.example"}):
            pass


def test_allowed_origin_is_accepted():
    client = make_client(FakeTranscriber(), cors_origins="http://localhost:5173")
    with client.websocket_connect("/ws/transcribe", headers={"origin": "http://localhost:5173"}) as ws:
        assert drain_until(ws, "ready")[0]["type"] == "ready"


def test_unknown_control_message_warns_and_ping_pongs():
    with make_client(FakeTranscriber()).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        ws.send_text("not json")
        assert drain_until(ws, "warning")[0]["code"] == "bad_message"
        ws.send_json({"type": "ping"})
        assert drain_until(ws, "pong")[0]["type"] == "pong"


def test_backlog_drops_oldest_chunk_with_warning():
    gate = threading.Event()
    t = FakeTranscriber(gate=gate)
    with make_client(t, max_pending_chunks=1).websocket_connect("/ws/transcribe") as ws:
        drain_until(ws, "ready")
        for _ in range(3):
            ws.send_bytes(FAKE_WEBM)
        warn, _ = drain_until(ws, "warning")
        assert warn["code"] == "backlog_dropped"
        gate.set()
        ws.send_json({"type": "stop"})
        drain_until(ws, "stopped")
    assert t.processed < 3  # at least one chunk was dropped


def test_client_disconnect_mid_transcription_does_not_hang():
    gate = threading.Event()
    t = FakeTranscriber(gate=gate)
    timer = threading.Timer(0.3, gate.set)  # let the in-flight worker thread finish
    timer.start()
    try:
        with make_client(t).websocket_connect("/ws/transcribe") as ws:
            drain_until(ws, "ready")
            ws.send_bytes(FAKE_WEBM)
        # leaving the context closes the socket while a chunk is being processed
    finally:
        timer.cancel()
        gate.set()
