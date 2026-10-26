import io

import numpy as np
import pytest

from app.config import Settings
from app.services.pipeline import ChunkPipeline
from app.services.transcription import (
    SAMPLE_RATE,
    Segment,
    TranscriptionError,
    WhisperTranscriber,
    sniff_container,
    trim_overlap,
)
from tests.helpers import FAKE_WEBM, make_webm


class FakeSeg:
    def __init__(self, text, start=0.0, end=1.0):
        self.text, self.start, self.end = text, start, end


class FakeModel:
    def __init__(self, segs=None, boom=False):
        self.segs, self.boom, self.calls = segs or [], boom, []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        if self.boom:
            raise RuntimeError("inference exploded")
        return iter(self.segs), object()


def settings(**kw):
    return Settings(_env_file=None, **kw)


def silence(seconds):
    return np.zeros(int(seconds * SAMPLE_RATE), dtype=np.float32)


# ---- container sniffing -----------------------------------------------------

@pytest.mark.parametrize("data,expected", [
    (b"\x1a\x45\xdf\xa3rest", "webm"),
    (b"OggSrest", "ogg"),
    (b"\x00\x00\x00\x18ftypmp42", "mp4"),
    (b"RIFF\x00\x00\x00\x00WAVEfmt ", "wav"),
    (b"not audio at all", None),
    (b"", None),
])
def test_sniff_container(data, expected):
    assert sniff_container(data) == expected


# ---- overlap trimming -------------------------------------------------------

def test_trim_overlap_removes_repeated_prefix_ignoring_case_and_punctuation():
    assert trim_overlap(["tell", "me", "about", "Your", "experience"], "your experience, with python") == "with python"


def test_trim_overlap_drops_fully_repeated_text():
    assert trim_overlap(["i", "built", "a", "parser"], "built a parser") == ""


def test_trim_overlap_keeps_single_repeated_word():
    assert trim_overlap(["no"], "no thanks") == "no thanks"


def test_trim_overlap_without_history_returns_text():
    assert trim_overlap([], " hello there ") == "hello there"


def test_trim_overlap_no_match_keeps_text():
    assert trim_overlap(["alpha", "beta"], "gamma delta") == "gamma delta"


# ---- WhisperTranscriber -----------------------------------------------------

def test_model_is_lazy_and_loaded_once():
    calls = []

    def factory():
        calls.append(1)
        return FakeModel()

    t = WhisperTranscriber(settings(), model_factory=factory)
    assert not t.loaded and calls == []
    t.ensure_loaded()
    t.ensure_loaded()
    assert t.loaded and len(calls) == 1


def test_model_load_failure_is_wrapped():
    def factory():
        raise OSError("no network")

    t = WhisperTranscriber(settings(), model_factory=factory)
    with pytest.raises(TranscriptionError) as e:
        t.ensure_loaded()
    assert e.value.code == "model_load_failed"
    assert not t.loaded


def test_decode_rejects_unknown_container_without_calling_decoder():
    def decoder(_):
        raise AssertionError("decoder should not run")

    t = WhisperTranscriber(settings(), model_factory=FakeModel, decoder=decoder)
    with pytest.raises(TranscriptionError) as e:
        t.decode(b"definitely not audio")
    assert e.value.code == "invalid_audio"


def test_decode_wraps_decoder_failure():
    def decoder(_):
        raise ValueError("bad stream")

    t = WhisperTranscriber(settings(), model_factory=FakeModel, decoder=decoder)
    with pytest.raises(TranscriptionError) as e:
        t.decode(FAKE_WEBM)
    assert e.value.code == "invalid_audio"


def test_real_webm_decodes_to_16k_float32_without_a_model():
    pytest.importorskip("faster_whisper")
    t = WhisperTranscriber(settings(), model_factory=FakeModel)  # default (real) decoder
    audio = t.decode(make_webm(1.5))
    assert audio.dtype == np.float32
    assert abs(audio.shape[0] / SAMPLE_RATE - 1.5) < 0.1


def test_real_decoder_rejects_corrupt_webm():
    pytest.importorskip("faster_whisper")
    t = WhisperTranscriber(settings(), model_factory=FakeModel)
    with pytest.raises(TranscriptionError) as e:
        t.decode(b"\x1a\x45\xdf\xa3" + b"garbage" * 50)
    assert e.value.code == "invalid_audio"


def test_too_short_audio_skips_model():
    created = []
    t = WhisperTranscriber(settings(), model_factory=lambda: created.append(1) or FakeModel())
    assert t.transcribe_audio(silence(0.1)) == []
    assert created == []


def test_transcribe_passes_settings_and_filters_empty_segments():
    model = FakeModel([FakeSeg("  hello world ", 0, 1), FakeSeg("   ", 1, 2), FakeSeg("bye", 2, 3)])
    t = WhisperTranscriber(
        settings(whisper_language="", whisper_beam_size=3, whisper_vad_filter=False),
        model_factory=lambda: model,
    )
    segs = t.transcribe_audio(silence(2))
    assert segs == [Segment("hello world", 0, 1), Segment("bye", 2, 3)]
    kw = model.calls[0]
    assert kw["language"] is None and kw["beam_size"] == 3 and kw["vad_filter"] is False
    assert kw["condition_on_previous_text"] is False


def test_inference_error_is_wrapped():
    t = WhisperTranscriber(settings(), model_factory=lambda: FakeModel(boom=True))
    with pytest.raises(TranscriptionError) as e:
        t.transcribe_audio(silence(2))
    assert e.value.code == "transcription_failed"


# ---- ChunkPipeline ----------------------------------------------------------

class ScriptedTranscriber:
    """Stands in for WhisperTranscriber; returns scripted segments per chunk."""

    def __init__(self, scripts, chunk_seconds=4.0):
        self.scripts, self.chunk_seconds, self.audio_lengths = list(scripts), chunk_seconds, []

    def decode(self, data):
        return silence(self.chunk_seconds)

    def transcribe_audio(self, audio):
        self.audio_lengths.append(audio.shape[0] / SAMPLE_RATE)
        return self.scripts.pop(0)


def test_pipeline_adds_overlap_and_drops_segments_inside_it():
    t = ScriptedTranscriber([
        [Segment("tell me about yourself", 0.0, 3.5)],
        # chunk 2 audio = 1s overlap + 4s new. First segment is entirely in the overlap.
        [Segment("yourself", 0.0, 0.9), Segment("i am an engineer", 1.2, 4.0)],
    ])
    p = ChunkPipeline(t, overlap_seconds=1.0)
    assert p.process(b"x") == ["tell me about yourself"]
    assert p.process(b"x") == ["i am an engineer"]
    assert t.audio_lengths == [4.0, 5.0]


def test_pipeline_trims_repeated_words_from_straddling_segment():
    t = ScriptedTranscriber([
        [Segment("walk me through your resume", 0.0, 3.9)],
        [Segment("your resume starting with school", 0.2, 4.5)],
    ])
    p = ChunkPipeline(t, overlap_seconds=1.0)
    p.process(b"x")
    assert p.process(b"x") == ["starting with school"]


def test_pipeline_without_overlap_passes_audio_through():
    t = ScriptedTranscriber([[Segment("one two", 0, 1)], [Segment("one two", 0, 1)]])
    p = ChunkPipeline(t, overlap_seconds=0)
    p.process(b"x")
    assert p.process(b"x") == []  # identical repeat of the previous tail is suppressed
    assert t.audio_lengths == [4.0, 4.0]


def test_pipeline_does_not_advance_state_when_transcription_fails():
    class Flaky(ScriptedTranscriber):
        def transcribe_audio(self, audio):
            if not self.scripts:
                raise TranscriptionError("transcription_failed", "x")
            return super().transcribe_audio(audio)

    t = Flaky([[Segment("hello there friend", 0, 3)]])
    p = ChunkPipeline(t, overlap_seconds=1.0)
    p.process(b"x")
    with pytest.raises(TranscriptionError):
        p.process(b"x")
    assert p._tail_words == ["hello", "there", "friend"]
