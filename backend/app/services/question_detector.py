"""Heuristic interview-question detection over CONFIRMED transcript text.

No LLM is involved. Text arrives in chunk-sized pieces, so a question may be split across
pieces or lack a question mark. The detector therefore:

  * buffers text and splits it into sentences on . ? ! (decimals/abbreviations without a
    following space are not split);
  * holds the final, unterminated sentence (or one ending in a dangling word such as
    "explain how") until more text arrives, the speaker pauses (`on_silence`), or the
    session flushes;
  * treats a sentence as a question when it ends in "?" or starts with an interview cue
    ("Tell me about...", "Explain...", "How would you...", "Do you have...");
  * joins several question sentences found in one step into one multi-part question;
  * suppresses repeats of the same question within a time window (overlapping audio,
    repeated transcript text).
"""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable

_SENT_SPLIT = re.compile(r"(?<=[.!?…])[\"')\]]*\s+")
_END_PUNCT = re.compile(r"[.!?…][\"')\]]*$")

_FILLERS = {"so", "okay", "ok", "alright", "right", "well", "um", "uh", "and", "now", "then",
            "great", "good", "thanks", "next", "also", "please", "yeah", "hmm", "like"}
_WH = {"what", "why", "how", "when", "where", "which", "who", "whom", "whose",
       "what's", "how's", "why's", "who's", "where's", "when's"}
_AUX = {"can", "could", "would", "will", "should", "do", "does", "did", "is", "are", "was",
        "were", "have", "has", "had", "may", "might", "shall"}
_SUBJ = {"you", "your", "we", "they", "there", "it", "this", "that", "the", "a", "an", "one",
         "any", "someone", "anyone", "he", "she"}
# "What I did...", "How we built..." are statements (usually the candidate speaking).
_WH_STATEMENT_SECOND = {"i", "we", "my", "our", "it", "he", "she", "they", "i'm", "i've", "i'd"}
_CUES = (
    "tell me", "tell us", "walk me through", "walk us through", "talk me through",
    "talk us through", "talk to me about", "talk about", "explain", "describe", "give me",
    "give us", "share", "let's talk about", "lets talk about", "i'd like you to",
    "i would like you to", "i want you to", "i'd like to hear", "i would like to hear",
    "i'd love to hear", "i'd like to know", "i would like to know", "i'm curious",
    "i am curious", "i was wondering", "i'm wondering", "compare", "define", "discuss",
    "outline", "summarize", "summarise", "elaborate",
)
_NOT_QUESTIONS_WITH_MARK = {"right", "okay", "ok", "yes", "no", "really", "sure", "correct",
                            "yeah", "you know", "see", "huh"}
# Audio checks, greeting small-talk and connection pings during interview setup are not interview questions.
_SMALL_TALK_OR_AUDIO_CHECKS = {
    "can you hear me", "can you hear me now", "are you able to hear me", "can you hear us",
    "can you see my screen", "is my screen visible", "can you see this", "are you there",
    "am i audible", "can everyone hear me", "how are you", "how are you doing",
    "how are you today", "how is it going", "good morning", "good afternoon", "good evening",
    "are you ready", "shall we begin", "ready to start", "testing testing", "one two three",
}
# A sentence ending in one of these was almost certainly cut off mid-question.
_DANGLING = {"the", "a", "an", "of", "and", "or", "your", "my", "our", "between", "how", "why",
             "whether", "if", "because", "but", "than"}


def _tok(word: str) -> str:
    return re.sub(r"^[^\w']+|[^\w']+$", "", word.lower().replace("’", "'"))


def _words(sentence: str) -> list[str]:
    return [t for t in (_tok(w) for w in sentence.split()) if t]


def _strip_fillers(words: list[str]) -> list[str]:
    i = 0
    while i < min(3, len(words) - 1) and words[i] in _FILLERS:
        i += 1
    return words[i:]


def is_question(sentence: str) -> bool:
    s = sentence.strip()
    words = _strip_fillers(_words(s))
    if not words:
        return False
    text = " ".join(words)
    if text in _SMALL_TALK_OR_AUDIO_CHECKS or any(text.startswith(c) for c in (
        "can you hear me", "can you see my screen", "how are you doing", "how are you today"
    )):
        return False
    first = words[0]
    if s.rstrip("\"')] ").endswith("?"):
        if " ".join(words) in _NOT_QUESTIONS_WITH_MARK or (len(words) <= 2 and first not in _WH):
            return False
        return len(words) >= 3 or first in _WH
    if len(words) >= 3 and first in _WH and words[1] not in _WH_STATEMENT_SECOND:
        return True
    if len(words) >= 3 and first in _AUX and words[1] in _SUBJ:
        return True
    if len(words) >= 2 and any(text == c or text.startswith(c + " ") for c in _CUES):
        return True
    return False


def looks_incomplete(sentence: str) -> bool:
    s = sentence.strip()
    if s.endswith(("…", "...")):
        return True
    if s.rstrip("\"')] ").endswith("?"):
        return False
    words = _words(s)
    return bool(words) and words[-1] in _DANGLING


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Return (terminated sentences, unterminated tail)."""
    parts = [p for p in _SENT_SPLIT.split(buffer.strip()) if p]
    if not parts:
        return [], ""
    if _END_PUNCT.search(parts[-1]):
        return parts, ""
    return parts[:-1], parts[-1]


@dataclass(frozen=True)
class Question:
    id: str
    text: str


def _key_set(text: str) -> set[str]:
    return set(_strip_fillers(_words(text)))


def _same_question(a: set[str], b: set[str]) -> bool:
    if not a or not b:
        return False
    if a == b:
        return True
    small = min(len(a), len(b))
    return small >= 3 and len(a & b) / small >= 0.9


class QuestionDetector:
    def __init__(
        self,
        dedupe_window_seconds: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        recent_chars: int = 1200,
    ):
        self._window = dedupe_window_seconds
        self._clock = clock
        self._recent_chars = recent_chars
        self._buffer = ""
        self._recent = ""
        self._seen: deque[tuple[set[str], float]] = deque()
        self._n = 0

    @property
    def pending(self) -> str:
        """Unterminated text still waiting for more speech."""
        return self._buffer

    def recent_transcript(self) -> str:
        return self._recent

    def feed(self, text: str) -> list[Question]:
        """Add confirmed transcript text. Returns newly detected questions (0 or 1)."""
        text = text.strip()
        if not text:
            return []
        self._recent = (f"{self._recent} {text}").strip()[-self._recent_chars:]
        sentences, tail = split_sentences(f"{self._buffer} {text}")
        if not tail and sentences and looks_incomplete(sentences[-1]):
            tail = sentences.pop().rstrip(".!…").rstrip()  # drop the stray stop so text can rejoin
        self._buffer = tail
        return self._emit(sentences)

    def on_silence(self) -> list[Question]:
        """The speaker paused (a chunk produced no new text): evaluate the held tail."""
        if not self._buffer:
            return []
        tail, self._buffer = self._buffer, ""
        return self._emit([tail])

    flush = on_silence

    def _emit(self, sentences: list[str]) -> list[Question]:
        found = [" ".join(s.split()) for s in sentences if is_question(s)]
        if not found:
            return []
        text = " ".join(found)
        keys = _key_set(text)
        now = self._clock()
        while self._seen and now - self._seen[0][1] > self._window:
            self._seen.popleft()
        if any(_same_question(keys, seen) for seen, _ in self._seen):
            return []
        self._seen.append((keys, now))
        self._n += 1
        return [Question(id=f"q{self._n}", text=text)]
