from app.config import Settings


def test_defaults():
    s = Settings(_env_file=None)
    assert s.cors_origin_list == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert (s.whisper_model, s.whisper_device, s.whisper_compute_type) == ("base", "cpu", "int8")
    assert not s.openrouter_configured
    assert s.openrouter_model == "openrouter/free"


def test_cors_origin_parsing_trims_and_skips_blanks():
    s = Settings(_env_file=None, cors_origins=" http://a.test , ,http://b.test ")
    assert s.cors_origin_list == ["http://a.test", "http://b.test"]


def test_env_vars_are_loaded(monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL", "small")
    monkeypatch.setenv("WHISPER_DEVICE", "cuda")
    monkeypatch.setenv("CORS_ORIGINS", "http://x.test")
    s = Settings(_env_file=None)
    assert (s.whisper_model, s.whisper_device) == ("small", "cuda")
    assert s.cors_origin_list == ["http://x.test"]


def test_whitespace_key_is_not_configured():
    assert not Settings(_env_file=None, openrouter_api_key="   ").openrouter_configured
