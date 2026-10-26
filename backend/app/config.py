"""Application settings loaded from environment variables / the root .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    # Comma-separated string (kept as str so .env values need no JSON quoting).
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # --- Local speech-to-text (faster-whisper). No cloud service or API key. ---
    whisper_model: str = "base"  # tiny | base | small | ... or a local model directory
    whisper_device: str = "cpu"  # cpu | cuda | auto
    whisper_compute_type: str = "int8"  # int8 is the best CPU default
    whisper_language: str = "en"  # blank = auto-detect (slower, less stable on short chunks)
    whisper_beam_size: int = 1  # 1 = fastest; raise for accuracy at the cost of latency
    whisper_vad_filter: bool = True  # skip silence; reduces hallucinated text
    whisper_cpu_threads: int = 0  # 0 = library default
    whisper_model_dir: str = ""  # blank = default Hugging Face cache

    # --- Chunk pipeline ---
    audio_overlap_seconds: float = 1.0  # tail of previous chunk re-fed for boundary context
    max_chunk_bytes: int = 5_000_000
    max_pending_chunks: int = 6  # backlog limit; oldest chunk is dropped beyond this
    stop_flush_timeout_seconds: float = 60.0

    # --- Question detection ---
    question_dedupe_seconds: float = 60.0  # ignore the same question again within this window

    # --- LLM answers via OpenRouter (key stays on the backend) ---
    openrouter_api_key: str = ""
    # "openrouter/free" lets OpenRouter pick any currently available free model.
    openrouter_model: str = "openrouter/free"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_timeout_seconds: float = 30.0  # max silence waiting for the provider
    openrouter_max_tokens: int = 350
    openrouter_temperature: float = 0.4
    openrouter_max_retries: int = 1  # retry 429/502/503/504 once, before any text arrived
    # Some free models reject a "system" message; set false to merge it into the user message.
    openrouter_use_system_role: bool = True
    answer_max_context_chars: int = 8000  # per field (resume, job description)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def openrouter_configured(self) -> bool:
        return bool(self.openrouter_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
