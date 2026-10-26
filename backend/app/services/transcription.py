"""Local speech-to-text with faster-whisper.

This is chunk-based transcription, not native streaming: each call receives a
complete, self-contained audio file (a full WebM/Ogg/MP4 container) and returns
text for that audio once inference finishes.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np

from app.config import Settings

logger = logging.getLogger("interview_assistant.transcription")

SAMPLE_RATE = 16000
MIN_AUDIO_SECONDS = 0.3


class TranscriptionError(Exception):
    """Raised with a stable machine-readable code and a user-safe message."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Segment:
    text: str
    start: float
    end: float


def sniff_container(data: bytes) -> str | None:
    """Cheaply identify a complete audio container by its magic bytes."""
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "webm"
    if data[:4] == b"OggS":
        return "ogg"
    if data[4:8] == b"ftyp":
        return "mp4"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    return None


def _key(token: str) -> str:
    return re.sub(r"[^\w']", "", token.lower())


def trim_overlap(previous_words: list[str], new_text: str, min_overlap: int = 2,
                 max_overlap: int = 15) -> str:
    """Remove a leading run of words in new_text that repeats the end of previous_words.

    Comparison ignores case and punctuation. Requires at least `min_overlap`
    matching words so a single repeated word ("no, no") is never swallowed.
    """
    tokens = new_text.split()
    if not tokens or not previous_words:
        return new_text.strip()
    tail = [_key(w) for w in previous_words]
    keys = [_key(t) for t in tokens]
    for n in range(min(len(tail), len(keys), max_overlap), min_overlap - 1, -1):
        if tail[-n:] == keys[:n]:
            return " ".join(tokens[n:])
    return new_text.strip()


def _default_decoder(fileobj: io.BytesIO) -> np.ndarray:
    from faster_whisper.audio import decode_audio  # lazy: heavy import

    return decode_audio(fileobj, sampling_rate=SAMPLE_RATE)


class WhisperTranscriber:
    def __init__(
        self,
        settings: Settings,
        model_factory: Callable[[], Any] | None = None,
        decoder: Callable[[io.BytesIO], np.ndarray] | None = None,
    ):
        self._s = settings
        self._model_factory = model_factory or self._default_factory
        self._decoder = decoder or _default_decoder
        self._model: Any = None
        self._load_lock = threading.Lock()
        self._infer_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def describe(self) -> dict[str, str]:
        return {
            "engine": "faster-whisper",
            "model": self._s.whisper_model,
            "device": self._s.whisper_device,
            "compute_type": self._s.whisper_compute_type,
        }

    def _default_factory(self) -> Any:
        from faster_whisper import WhisperModel  # lazy: heavy import

        kwargs: dict[str, Any] = {
            "device": self._s.whisper_device,
            "compute_type": self._s.whisper_compute_type,
            "cpu_threads": self._s.whisper_cpu_threads,
        }
        if self._s.whisper_model_dir.strip():
            kwargs["download_root"] = self._s.whisper_model_dir.strip()
        return WhisperModel(self._s.whisper_model, **kwargs)

    def ensure_loaded(self) -> None:
        """Load (and on first run, download) the model. Blocking; call off the event loop."""
        with self._load_lock:
            if self._model is not None:
                return
            logger.info(
                "Loading Whisper model '%s' (device=%s, compute_type=%s). "
                "The first run downloads the model.",
                self._s.whisper_model, self._s.whisper_device, self._s.whisper_compute_type,
            )
            started = time.monotonic()
            try:
                self._model = self._model_factory()
            except Exception as exc:
                logger.exception("Whisper model failed to load")
                raise TranscriptionError(
                    "model_load_failed",
                    f"Could not load Whisper model '{self._s.whisper_model}'. Check the model "
                    "name, your internet connection for the first download, and that "
                    "WHISPER_DEVICE / WHISPER_COMPUTE_TYPE are valid. See the backend log.",
                ) from exc
            logger.info("Whisper model ready in %.1fs", time.monotonic() - started)

    def decode(self, data: bytes) -> np.ndarray:
        if sniff_container(data) is None:
            raise TranscriptionError("invalid_audio", "Audio chunk is not a recognised container.")
        try:
            return np.asarray(self._decoder(io.BytesIO(data)), dtype=np.float32)
        except Exception as exc:
            logger.warning("Audio chunk failed to decode (%d bytes): %s", len(data), exc)
            raise TranscriptionError("invalid_audio", "Audio chunk could not be decoded.") from exc

    def transcribe_audio(self, audio: np.ndarray) -> list[Segment]:
        """Transcribe 16 kHz mono float32 audio. Blocking; call off the event loop."""
        if audio.shape[0] < MIN_AUDIO_SECONDS * SAMPLE_RATE:
            return []
        self.ensure_loaded()
        started = time.monotonic()
        try:
            with self._infer_lock:
                segments, _info = self._model.transcribe(
                    audio,
                    language=self._s.whisper_language.strip() or None,
                    beam_size=self._s.whisper_beam_size,
                    vad_filter=self._s.whisper_vad_filter,
                    condition_on_previous_text=False,
                    temperature=0.0,
                )
                result = [
                    Segment(text=s.text.strip(), start=float(s.start), end=float(s.end))
                    for s in segments
                    if s.text and s.text.strip()
                ]
        except Exception as exc:
            logger.exception("Whisper inference failed")
            raise TranscriptionError("transcription_failed", "Transcription failed on this chunk.") from exc
        logger.info(
            "Transcribed %.1fs audio in %.2fs (%d segments)",
            audio.shape[0] / SAMPLE_RATE, time.monotonic() - started, len(result),
        )
        return result
