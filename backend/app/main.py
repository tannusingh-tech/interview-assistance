import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import Settings, get_settings
from app.routers import health, transcribe, manual
from app.services.answers import OpenRouterClient
from app.services.transcription import WhisperTranscriber

logger = logging.getLogger("interview_assistant")


def create_app(settings: Settings | None = None, transcriber=None, answer_client=None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    app = FastAPI(title="Real-Time Interview Assistant API", version=__version__)
    app.state.settings = settings
    # The model is loaded lazily on the first session, not at import/startup.
    app.state.transcriber = transcriber or WhisperTranscriber(settings)
    app.state.answer_client = answer_client or OpenRouterClient(settings)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(transcribe.router)
    app.include_router(manual.router)

    logger.info(
        "API ready (env=%s, cors_origins=%s, whisper_model=%s, device=%s, compute_type=%s, "
        "openrouter_configured=%s, openrouter_model=%s)",
        settings.app_env, settings.cors_origin_list, settings.whisper_model,
        settings.whisper_device, settings.whisper_compute_type,
        settings.openrouter_configured, settings.openrouter_model,
    )
    return app


app = create_app()
