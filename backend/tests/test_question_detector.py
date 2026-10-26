import pytest

from app.services.question_detector import QuestionDetector, is_question, split_sentences


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def texts(found):
    return [q.text for q in found]


@pytest.mark.parametrize("sentence", [
    "Tell me about yourself.",
    "Tell me about yourself",
    "Explain how RAG works",
    "Walk me through your resume.",
    "Describe a time you handled conflict.",
    "How would you design a rate limiter?",
    "How would you design a rate limiter",
    "Can you explain the difference between a process and a thread?",
    "Do you have experience with Kubernetes?",
    "Have you ever worked with distributed systems",
    "What is the difference between TCP and UDP",
    "Why did you leave your last job.",
    "Okay, so tell me about your biggest weakness.",
    "I'd like to hear about a project you're proud of.",
    "You've worked with Kubernetes?",
    "Why?",
    "What are your salary expectations?",
])
def test_detects_questions(sentence):
    assert is_question(sentence)


@pytest.mark.parametrize("sentence", [
    "Thanks for joining us today.",
    "I worked on a payments platform at my previous company.",
    "What I did was build the API.",
    "How we handled it was with retries.",
    "Right?",
    "Okay.",
    "You know?",
    "That makes sense.",
    "I explained the design to the team.",
    "Explain",
    "We use Python and Go.",
    "",
])
def test_ignores_non_questions(sentence):
    assert not is_question(sentence)


def test_split_sentences_keeps_decimals_and_returns_tail():
    sents, tail = split_sentences("Version 3.5 is out. Can you explain how")
    assert sents == ["Version 3.5 is out."]
    assert tail == "Can you explain how"


def test_question_with_mark_is_detected_immediately():
    d = QuestionDetector()
    assert texts(d.feed("How would you design a rate limiter?")) == ["How would you design a rate limiter?"]


def test_question_split_across_chunks_is_joined():
    d = QuestionDetector()
    assert d.feed("Can you explain how") == []
    assert d.pending == "Can you explain how"
    assert texts(d.feed("RAG works?")) == ["Can you explain how RAG works?"]
    assert d.pending == ""


def test_stray_period_on_cut_off_chunk_does_not_split_the_question():
    d = QuestionDetector()
    assert d.feed("Can you explain how.") == []
    assert d.pending == "Can you explain how"
    assert texts(d.feed("RAG works?")) == ["Can you explain how RAG works?"]


def test_unterminated_continuation_across_chunks():
    d = QuestionDetector()
    assert d.feed("Tell me about") == []
    assert texts(d.feed("your experience with Python.")) == ["Tell me about your experience with Python."]


def test_question_without_mark_waits_for_silence():
    d = QuestionDetector()
    assert d.feed("Tell me about yourself") == []
    assert texts(d.on_silence()) == ["Tell me about yourself"]
    assert d.on_silence() == []


def test_unterminated_statement_is_dropped_on_silence():
    d = QuestionDetector()
    d.feed("I built the payments service")
    assert d.on_silence() == []
    assert d.pending == ""


def test_greeting_before_question_is_not_included():
    d = QuestionDetector()
    assert texts(d.feed("Hi, thanks for joining. Tell me about yourself.")) == ["Tell me about yourself."]


def test_multi_part_question_is_one_request():
    d = QuestionDetector()
    found = d.feed("What's your experience with Python? And how long have you used it?")
    assert len(found) == 1
    assert "experience with Python" in found[0].text and "how long" in found[0].text


def test_exact_repeat_is_suppressed_then_allowed_after_window():
    clock = Clock()
    d = QuestionDetector(dedupe_window_seconds=60, clock=clock)
    assert len(d.feed("Tell me about yourself.")) == 1
    assert d.feed("Tell me about yourself.") == []
    clock.t += 30
    assert d.feed("Tell me about yourself.") == []  # still inside the window
    clock.t += 61
    again = d.feed("Tell me about yourself.")
    assert len(again) == 1 and again[0].id == "q2"


def test_repeat_with_leading_filler_or_extra_words_is_suppressed():
    d = QuestionDetector()
    assert len(d.feed("Can you explain how RAG works?")) == 1
    assert d.feed("Okay, so can you explain how RAG works?") == []
    assert d.feed("Explain how RAG works.") == []


def test_similar_but_different_questions_are_both_detected():
    d = QuestionDetector()
    assert [q.id for q in d.feed("What is a list?")] == ["q1"]
    assert [q.id for q in d.feed("What is a tuple?")] == ["q2"]


def test_recent_transcript_is_bounded_and_ordered():
    d = QuestionDetector(recent_chars=40)
    d.feed("first sentence here.")
    d.feed("second sentence here.")
    d.feed("third sentence here.")
    recent = d.recent_transcript()
    assert len(recent) <= 40 and recent.endswith("third sentence here.")


def test_audio_check_and_setup_smalltalk_are_ignored():
    d = QuestionDetector()
    assert d.feed("Can you hear me?") == []
    assert d.feed("Can you see my screen?") == []
    assert d.feed("Are you able to hear me?") == []
    assert d.feed("How are you doing today?") == []
    # Real interview question following audio check should be detected
    assert len(d.feed("Tell me about yourself.")) == 1
