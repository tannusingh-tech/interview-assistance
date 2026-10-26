from fastapi import APIRouter, Request

from app import __version__

router = APIRouter()


@router.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "interview-assistant-api", "version": __version__}


@router.get("/api/config", tags=["system"])
async def public_config(request: Request) -> dict:
    """Non-secret runtime info. Never returns API keys."""
    settings = request.app.state.settings
    transcriber = request.app.state.transcriber
    return {
        "environment": settings.app_env,
        "transcription": {**transcriber.describe(), "model_loaded": transcriber.loaded},
        "answers": {"provider": "openrouter", "model": settings.openrouter_model,
                    "configured": settings.openrouter_configured},
    }
