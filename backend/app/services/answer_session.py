"""Runs at most one answer generation per session and streams it to the client."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Awaitable, Callable

from app.services.answers import AnswerError, build_messages
from app.services.question_detector import Question

logger = logging.getLogger("interview_assistant.answers")

Send = Callable[[dict], Awaitable[bool]]


class AnswerCoordinator:
    """A new question cancels the previous answer, so a stale answer can never keep streaming."""

    def __init__(self, client, send: Send, max_context_chars: int = 8000, use_system_role: bool = True):
        self._client = client
        self._send = send
        self._max_chars = max_context_chars
        self._use_system_role = use_system_role
        self._task: asyncio.Task | None = None
        self._current_id: str | None = None
        self.resume = ""
        self.job_description = ""
        self.mode = "concise"

    def set_context(self, resume: str, job_description: str, mode: str = "concise") -> None:
        self.resume, self.job_description = resume, job_description
        if mode:
            self.mode = mode
        logger.info("Context updated (resume=%d chars, job_description=%d chars, mode=%s)",
                    len(resume), len(job_description), self.mode)

    async def submit(self, question: Question, recent_transcript: str) -> None:
        if question.id == self._current_id:
            return  # never start the same question twice
        await self.cancel()
        self._current_id = question.id
        # Snapshot context now so later edits cannot change an answer already in progress.
        messages = None
        if getattr(self._client, "configured", False):
            messages = build_messages(question.text, self.resume, self.job_description,
                                      recent_transcript, self._max_chars, self._use_system_role,
                                      mode=self.mode)
        self._task = asyncio.create_task(self._run(question.id, messages))

    async def cancel(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _run(self, qid: str, messages: list[dict] | None) -> None:
        started = time.monotonic()
        chars = 0
        ttft_ms: int | None = None
        try:
            if messages is None:
                raise AnswerError("not_configured", "AI answers are off: set OPENROUTER_API_KEY in .env "
                                  "(get one at https://openrouter.ai/keys) and restart the backend.")
            await self._send({"type": "answer_start", "question_id": qid})
            async for delta in self._client.stream(messages):
                if ttft_ms is None:
                    ttft_ms = round((time.monotonic() - started) * 1000)
                chars += len(delta)
                if not await self._send({"type": "answer_delta", "question_id": qid, "text": delta}):
                    return  # client is gone
            total_ms = round((time.monotonic() - started) * 1000)
            latency = {"ttft_ms": ttft_ms or total_ms, "total_ms": total_ms}
            await self._send({"type": "answer_done", "question_id": qid, "latency": latency})
            logger.info("Answer %s finished: %d chars in %.1fs (TTFT: %dms, Total: %dms)",
                        qid, chars, time.monotonic() - started, latency["ttft_ms"], total_ms)
        except asyncio.CancelledError:
            logger.info("Answer %s cancelled after %d chars", qid, chars)
            raise
        except AnswerError as exc:
            # Retry if the provider returned an empty response (non-transient)
            if exc.code == "empty_response":
                # Allow a single retry for empty responses
                logger.warning("Empty answer for %s; retrying once", qid)
                try:
                    retry_ttft_ms: int | None = None
                    async for delta in self._client.stream(messages):
                        if retry_ttft_ms is None:
                            retry_ttft_ms = round((time.monotonic() - started) * 1000)
                        chars += len(delta)
                        if not await self._send({"type": "answer_delta", "question_id": qid, "text": delta}):
                            return
                    total_ms = round((time.monotonic() - started) * 1000)
                    latency = {"ttft_ms": retry_ttft_ms or total_ms, "total_ms": total_ms}
                    await self._send({"type": "answer_done", "question_id": qid, "latency": latency})
                    logger.info("Answer %s (retry) finished: %d chars in %.1fs (TTFT: %dms, Total: %dms)",
                                qid, chars, time.monotonic() - started, latency["ttft_ms"], total_ms)
                    return
                except AnswerError as retry_exc:
                    # If retry also fails, fall through to error handling below
                    exc = retry_exc
            logger.warning("Answer %s failed: %s", qid, exc.code)
            await self._send({"type": "answer_error", "question_id": qid, "code": exc.code,
                              "message": exc.message})
        except Exception:
            logger.exception("Unexpected failure generating answer %s", qid)
            await self._send({"type": "answer_error", "question_id": qid, "code": "internal_error",
                              "message": "Unexpected error while generating the answer."})
