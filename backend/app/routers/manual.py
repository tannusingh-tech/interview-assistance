from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
import logging
import time

from app.config import Settings, get_settings
from app.services.answers import OpenRouterClient, build_messages, AnswerError

router = APIRouter()
logger = logging.getLogger("interview_assistant.manual")

class ManualQuestion(BaseModel):
    question: str
    resume: str = ""
    job_description: str = ""
    mode: str = "concise"

@router.post("/manual_question")
async def manual_question_endpoint(payload: ManualQuestion, request: Request):
    """Generate an answer for a manually provided question.
    Returns the full answer text and latency metrics or raises an HTTPException on error.
    """
    settings: Settings = getattr(request.app.state, "settings", None) or get_settings()
    client = getattr(request.app.state, "answer_client", None) or OpenRouterClient(settings)
    if not client.configured:
        raise HTTPException(status_code=503, detail="AI answers are off. OpenRouter not configured.")
    messages = build_messages(
        payload.question,
        payload.resume,
        payload.job_description,
        recent_transcript="",
        max_chars=8000,
        use_system_role=settings.openrouter_use_system_role,
        mode=payload.mode or "concise",
    )
    started = time.monotonic()
    ttft_ms: int | None = None
    answer_parts = []
    try:
        async for delta in client.stream(messages):
            if ttft_ms is None:
                ttft_ms = round((time.monotonic() - started) * 1000)
            answer_parts.append(delta)
    except AnswerError as exc:
        # Retry once if the provider returned an empty response
        if exc.code == "empty_response":
            logger.warning("Empty answer for manual question; retrying once")
            try:
                retry_ttft_ms: int | None = None
                async for delta in client.stream(messages):
                    if retry_ttft_ms is None:
                        retry_ttft_ms = round((time.monotonic() - started) * 1000)
                    answer_parts.append(delta)
                ttft_ms = retry_ttft_ms
            except AnswerError as retry_exc:
                logger.error("Manual question retry failed: %s", retry_exc.message)
                raise HTTPException(status_code=500, detail=retry_exc.message)
        else:
            logger.error("Manual question generation failed: %s", exc.message)
            raise HTTPException(status_code=500, detail=exc.message)
    full_answer = "".join(answer_parts)
    if not full_answer:
        raise HTTPException(status_code=500, detail="Empty answer from provider.")
    total_ms = round((time.monotonic() - started) * 1000)
    latency = {"ttft_ms": ttft_ms or total_ms, "total_ms": total_ms}
    return {"answer": full_answer, "latency": latency}
