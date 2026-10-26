from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def make_client(**overrides) -> TestClient:
    # _env_file=None keeps tests independent of any real .env on the machine.
    settings = Settings(_env_file=None, **overrides)
    return TestClient(create_app(settings))


def test_health_returns_ok():
    res = make_client().get("/health")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "ok"
    assert body["service"] == "interview-assistant-api"
    assert body["version"]


def test_cors_allows_configured_origin():
    client = make_client(cors_origins="http://localhost:5173")
    res = client.get("/health", headers={"Origin": "http://localhost:5173"})
    assert res.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_preflight_allowed_origin():
    client = make_client(cors_origins="http://localhost:5173")
    res = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert res.status_code == 200
    assert res.headers["access-control-allow-origin"] == "http://localhost:5173"


def test_cors_rejects_unconfigured_origin():
    client = make_client(cors_origins="http://localhost:5173")
    res = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in res.headers


def test_public_config_reports_transcription_info_without_leaking_keys():
    client = make_client(openrouter_api_key="sk-or-secret-123", whisper_model="tiny")
    res = client.get("/api/config")
    assert res.status_code == 200
    body = res.json()
    assert body["transcription"]["engine"] == "faster-whisper"
    assert body["transcription"]["model"] == "tiny"
    assert body["transcription"]["model_loaded"] is False  # lazy: nothing loaded at startup
    assert body["answers"] == {"provider": "openrouter", "model": "openrouter/free", "configured": True}
    assert "sk-or-secret-123" not in res.text


def test_unknown_route_404():
    assert make_client().get("/nope").status_code == 404


def test_manual_question_returns_503_when_not_configured():
    client = make_client(openrouter_api_key="")
    res = client.post("/manual_question", json={"question": "Tell me about yourself"})
    assert res.status_code == 503
    assert "OpenRouter not configured" in res.json()["detail"]
