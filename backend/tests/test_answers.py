import asyncio
import json
import logging

import httpx
import pytest

from app.config import Settings
from app.services.answer_session import AnswerCoordinator
from app.services.answers import AnswerError, OpenRouterClient, SYSTEM_PROMPT, build_messages
from app.services.question_detector import Question

KEY = "sk-or-test-SECRET-123"


def settings(**kw):
    return Settings(_env_file=None, openrouter_api_key=kw.pop("key", KEY), **kw)


def delta(text):
    return {"choices": [{"delta": {"content": text}}]}


def sse(*objs, done=True, extra_lines=()):
    body = "".join(f"data: {json.dumps(o)}\n\n" if not isinstance(o, str) else f"{o}\n\n" for o in objs)
    body += "".join(f"{line}\n\n" for line in extra_lines)
    return (body + ("data: [DONE]\n\n" if done else "")).encode()


def make_client(handler, **kw):
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)

    client = OpenRouterClient(settings(**kw), transport=httpx.MockTransport(handler), sleep=fake_sleep)
    return client, sleeps


def collect(client, messages=None):
    async def run():
        return [d async for d in client.stream(messages or [{"role": "user", "content": "hi"}])]

    return asyncio.run(run())


def error_of(client):
    with pytest.raises(AnswerError) as e:
        collect(client)
    return e.value


def ok_response(*texts):
    return httpx.Response(200, content=sse(*[delta(t) for t in texts]),
                          headers={"content-type": "text/event-stream"})


# ---- streaming client -------------------------------------------------------

def test_streams_deltas_and_sends_expected_request():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, content=sse(
            ": OPENROUTER PROCESSING", delta("Hello "),
            {"choices": [{"delta": {"reasoning": "thinking...", "content": None}}]},
            delta("world."), {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            extra_lines=["data: {not json"],
        ))

    client, _ = make_client(handler, openrouter_model="some/model:free")
    assert collect(client, [{"role": "user", "content": "q"}]) == ["Hello ", "world."]
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["auth"] == f"Bearer {KEY}"
    assert seen["body"]["stream"] is True and seen["body"]["model"] == "some/model:free"
    assert seen["body"]["messages"] == [{"role": "user", "content": "q"}]


def test_not_configured_makes_no_http_call():
    calls = []
    client, _ = make_client(lambda r: calls.append(r) or ok_response("x"), key="")
    assert error_of(client).code == "not_configured"
    assert calls == []


@pytest.mark.parametrize("status,code", [
    (401, "auth_failed"), (403, "auth_failed"), (402, "insufficient_credits"),
    (404, "model_unavailable"), (408, "timeout"), (500, "provider_error"), (418, "http_error"),
])
def test_http_errors_map_to_codes_and_never_leak_the_key(status, code, caplog):
    caplog.set_level(logging.DEBUG)
    client, _ = make_client(lambda r: httpx.Response(status, json={"error": {"message": "nope"}}))
    err = error_of(client)
    assert err.code == code
    assert KEY not in err.message and KEY not in caplog.text


def test_bad_request_about_system_role_gives_actionable_hint():
    client, _ = make_client(lambda r: httpx.Response(
        400, json={"error": {"message": "Developer instruction is not enabled for this model"}}))
    err = error_of(client)
    assert err.code == "bad_request" and "OPENROUTER_USE_SYSTEM_ROLE=false" in err.message


def test_rate_limit_retries_once_honouring_retry_after_then_succeeds():
    calls = []

    def handler(request):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "2"}, json={"error": {"message": "slow down"}})
        return ok_response("done")

    client, sleeps = make_client(handler)
    assert collect(client) == ["done"]
    assert len(calls) == 2 and sleeps == [2.0]


def test_rate_limit_gives_up_after_configured_retries():
    calls = []
    client, sleeps = make_client(lambda r: calls.append(1) or httpx.Response(429, json={}), openrouter_max_retries=1)
    err = error_of(client)
    assert err.code == "rate_limited" and len(calls) == 2 and len(sleeps) == 1
    assert "rate limit" in err.message.lower()


def test_retries_can_be_disabled():
    calls = []
    client, _ = make_client(lambda r: calls.append(1) or httpx.Response(429, json={}), openrouter_max_retries=0)
    assert error_of(client).code == "rate_limited" and len(calls) == 1


def test_503_is_retried_but_500_is_not():
    calls = []

    def flaky(request):
        calls.append(1)
        return httpx.Response(503, json={}) if len(calls) == 1 else ok_response("ok")

    client, _ = make_client(flaky)
    assert collect(client) == ["ok"]

    calls.clear()
    client, _ = make_client(lambda r: calls.append(1) or httpx.Response(500, json={}))
    assert error_of(client).code == "provider_error" and len(calls) == 1


def test_timeout_and_network_errors():
    def slow(request):
        raise httpx.ReadTimeout("timed out", request=request)

    def down(request):
        raise httpx.ConnectError("refused", request=request)

    assert error_of(make_client(slow)[0]).code == "timeout"
    assert error_of(make_client(down)[0]).code == "network"


def test_error_in_stream_before_text_is_reported():
    body = sse({"error": {"code": 502, "message": "upstream exploded"}, "choices": [{"finish_reason": "error"}]})
    client, _ = make_client(lambda r: httpx.Response(200, content=body))
    err = error_of(client)
    assert err.code == "provider_error" and "upstream exploded" in err.message


def test_error_after_text_is_not_retried_or_duplicated():
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(200, content=sse(delta("partial "), {"error": {"code": 429, "message": "x"}}))

    client, sleeps = make_client(handler)
    got = []

    async def run():
        async for d in client.stream([{"role": "user", "content": "q"}]):
            got.append(d)

    with pytest.raises(AnswerError) as e:
        asyncio.run(run())
    assert got == ["partial "] and e.value.code == "rate_limited" and len(calls) == 1 and sleeps == []


def test_empty_stream_is_an_error():
    client, _ = make_client(lambda r: httpx.Response(200, content=sse()))
    assert error_of(client).code == "empty_response"


# ---- prompt construction ----------------------------------------------------

def test_prompt_includes_context_and_forbids_fabrication():
    msgs = build_messages("Why Python?", "Built ETL at Acme.", "Backend engineer, Go", "recent words")
    assert msgs[0]["role"] == "system" and msgs[1]["role"] == "user"
    user = msgs[1]["content"]
    assert "Built ETL at Acme." in user and "Backend engineer, Go" in user and "recent words" in user
    assert user.rstrip().endswith("Why Python?")
    assert "Never invent" in SYSTEM_PROMPT and "[add a real example" in SYSTEM_PROMPT


def test_prompt_marks_missing_context_and_truncates_long_context():
    user = build_messages("Q?", "", "", "")[1]["content"]
    assert user.count("(not provided)") == 2
    long = build_messages("Q?", "x" * 500, "", "", max_chars=100)[1]["content"]
    assert "[truncated]" in long and "x" * 101 not in long


def test_system_role_can_be_merged_into_user_message():
    msgs = build_messages("Q?", "r", "j", "t", use_system_role=False)
    assert len(msgs) == 1 and msgs[0]["role"] == "user" and "real-time interview coach" in msgs[0]["content"]


def test_build_messages_supports_different_modes():
    concise = build_messages("Q?", "r", "j", "t", mode="concise")[0]["content"]
    assert "CONCISE" in concise
    star = build_messages("Q?", "r", "j", "t", mode="star")[0]["content"]
    assert "STAR METHOD" in star
    detailed = build_messages("Q?", "r", "j", "t", mode="detailed")[0]["content"]
    assert "DETAILED" in detailed


# ---- AnswerCoordinator ------------------------------------------------------

class FakeLLM:
    configured = True
    model = "fake"

    def __init__(self, script=("A", "B"), error=None, hold=False):
        self.script, self.error, self.hold = script, error, hold
        self.calls, self.cancelled = [], []

    async def stream(self, messages):
        self.calls.append(messages)
        try:
            for i, d in enumerate(self.script):
                yield d
                if self.hold and i == 0:
                    await asyncio.sleep(3600)
            if self.error:
                raise self.error
        except asyncio.CancelledError:
            self.cancelled.append(len(self.calls))
            raise


def run_coordinator(llm, steps):
    events = []

    async def send(p):
        events.append(p)
        return True

    async def main():
        co = AnswerCoordinator(llm, send)
        co.set_context("RESUME-TEXT", "JD-TEXT")
        await steps(co)
        return co

    asyncio.run(main())
    return events


def test_coordinator_streams_events_tagged_with_question_id():
    llm = FakeLLM()

    async def steps(co):
        await co.submit(Question("q1", "Why?"), "ctx")
        await co._task

    events = run_coordinator(llm, steps)
    assert [e["type"] for e in events] == ["answer_start", "answer_delta", "answer_delta", "answer_done"]
    assert {e["question_id"] for e in events} == {"q1"}
    assert "".join(e["text"] for e in events if e["type"] == "answer_delta") == "AB"
    assert "RESUME-TEXT" in llm.calls[0][1]["content"] and "JD-TEXT" in llm.calls[0][1]["content"]


def test_coordinator_ignores_resubmission_of_same_question():
    llm = FakeLLM()

    async def steps(co):
        q = Question("q1", "Why?")
        await co.submit(q, "")
        await co.submit(q, "")
        await co._task

    run_coordinator(llm, steps)
    assert len(llm.calls) == 1


def test_new_question_cancels_the_stale_answer():
    llm = FakeLLM(hold=True)

    async def steps(co):
        await co.submit(Question("q1", "First?"), "")
        await asyncio.sleep(0.05)
        llm.hold = False
        await co.submit(Question("q2", "Second?"), "")
        await co._task

    events = run_coordinator(llm, steps)
    assert llm.cancelled == [1]
    assert not any(e["type"] == "answer_done" and e["question_id"] == "q1" for e in events)
    assert events[-1]["type"] == "answer_done" and events[-1]["question_id"] == "q2"
    assert "latency" in events[-1] and "ttft_ms" in events[-1]["latency"]


def test_coordinator_reports_provider_errors_for_the_right_question():
    llm = FakeLLM(script=(), error=AnswerError("rate_limited", "slow down"))

    async def steps(co):
        await co.submit(Question("q7", "Why?"), "")
        await co._task

    events = run_coordinator(llm, steps)
    assert events[-1] == {"type": "answer_error", "question_id": "q7", "code": "rate_limited", "message": "slow down"}


def test_coordinator_turns_unexpected_exceptions_into_safe_errors():
    llm = FakeLLM(script=(), error=RuntimeError("boom with secrets"))

    async def steps(co):
        await co.submit(Question("q1", "Why?"), "")
        await co._task

    last = run_coordinator(llm, steps)[-1]
    assert last["code"] == "internal_error" and "secrets" not in last["message"]


def test_coordinator_without_configured_client_reports_not_configured():
    llm = FakeLLM()
    llm.configured = False

    async def steps(co):
        await co.submit(Question("q1", "Why?"), "")
        await co._task

    events = run_coordinator(llm, steps)
    assert events == [{"type": "answer_error", "question_id": "q1", "code": "not_configured",
                       "message": events[0]["message"]}]
    assert llm.calls == []
