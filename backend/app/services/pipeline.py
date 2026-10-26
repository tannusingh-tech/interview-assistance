"""Per-session chunk pipeline: decode, add overlap context, transcribe, de-duplicate."""

from __future__ import annotations

import numpy as np

from app.services.transcription import SAMPLE_RATE, WhisperTranscriber, trim_overlap

MAX_TAIL_WORDS = 20
_EPS = 0.05  # seconds of tolerance when comparing segment times to the overlap boundary


class ChunkPipeline:
    """Holds state across consecutive chunks of ONE session. Not thread-safe; one worker uses it."""

    def __init__(self, transcriber: WhisperTranscriber, overlap_seconds: float = 1.0):
        self._t = transcriber
        self._overlap_samples = max(0, int(overlap_seconds * SAMPLE_RATE))
        self._tail_audio: np.ndarray | None = None
        self._tail_words: list[str] = []

    def process(self, data: bytes) -> list[str]:
        """Return new, de-duplicated text pieces for one complete audio chunk (blocking)."""
        audio = self._t.decode(data)

        offset = 0.0
        combined = audio
        if self._tail_audio is not None and self._tail_audio.size:
            combined = np.concatenate([self._tail_audio, audio])
            offset = self._tail_audio.size / SAMPLE_RATE

        segments = self._t.transcribe_audio(combined)

        # Only advance overlap state once decode + transcription both succeeded.
        if self._overlap_samples:
            self._tail_audio = audio[-self._overlap_samples:]

        pieces: list[str] = []
        for seg in segments:
            if offset and seg.end <= offset + _EPS:
                continue  # lies entirely in audio already transcribed with the previous chunk
            text = trim_overlap(self._tail_words, seg.text)
            if not text:
                continue
            pieces.append(text)
            self._tail_words = (self._tail_words + text.split())[-MAX_TAIL_WORDS:]
        return pieces
