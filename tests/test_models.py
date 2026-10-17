"""Tests for `aldwin.core.models` (`check_key`, `list_chat_models`, `RetryingModel`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

import httpx
import openai
import pytest
from agentling import ChatMessage, Delta
from fakes.model import FakeModel, FakeTurn

from aldwin.core.models import (
    KeyStatus,
    RetryingModel,
    TransientModelError,
    build_openai_model,
    check_key,
    classify_model_error,
    is_chat_model,
    list_chat_models,
)


@dataclass
class _StubModelEntry:
    id: str
    created: int


@dataclass
class _StubModelPage:
    data: list[_StubModelEntry]


class _StubModelsAPI:
    def __init__(self, outcome: _StubModelPage | Exception) -> None:
        self._outcome = outcome

    async def list(self) -> _StubModelPage:
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome


class _StubOpenAIClient:
    def __init__(self, outcome: _StubModelPage | Exception) -> None:
        self.models = _StubModelsAPI(outcome)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _http_response(status_code: int) -> httpx.Response:
    req = httpx.Request("GET", "https://api.openai.com/v1/models")
    return httpx.Response(status_code, request=req)


async def test_check_key_statuses() -> None:
    """TC-08: `check_key` returns `invalid`, `no_access`, `network_error`, and `ok`."""
    assert await check_key("") == KeyStatus.INVALID
    assert await check_key("   ") == KeyStatus.INVALID

    err_401 = openai.AuthenticationError(
        "Incorrect API key provided: sk-secret1234567890",
        response=cast(Any, _http_response(401)),
        body=None,
    )
    client_401 = _StubOpenAIClient(err_401)
    assert await check_key("sk-test", client=cast(Any, client_401)) == KeyStatus.INVALID

    err_403 = openai.PermissionDeniedError(
        "Project has no access",
        response=cast(Any, _http_response(403)),
        body=None,
    )
    client_403 = _StubOpenAIClient(err_403)
    assert (
        await check_key("sk-test", client=cast(Any, client_403)) == KeyStatus.NO_ACCESS
    )

    client_no_chat = _StubOpenAIClient(
        _StubModelPage(data=[_StubModelEntry(id="text-embedding-3-small", created=10)])
    )
    assert (
        await check_key("sk-test", client=cast(Any, client_no_chat))
        == KeyStatus.NO_ACCESS
    )

    req = httpx.Request("GET", "https://api.openai.com/v1/models")
    err_net = openai.APIConnectionError(request=cast(Any, req))
    client_net = _StubOpenAIClient(err_net)
    assert (
        await check_key("sk-test", client=cast(Any, client_net))
        == KeyStatus.NETWORK_ERROR
    )

    client_timeout = _StubOpenAIClient(TimeoutError("timed out"))
    assert (
        await check_key("sk-test", client=cast(Any, client_timeout))
        == KeyStatus.NETWORK_ERROR
    )

    client_ok = _StubOpenAIClient(
        _StubModelPage(data=[_StubModelEntry(id="gpt-4o", created=200)])
    )
    assert await check_key("sk-test", client=cast(Any, client_ok)) == KeyStatus.OK


async def test_list_chat_models_filters_non_chat_and_sorts_newest_first() -> None:
    """TC-17: `list_chat_models` excludes non-chat models and sorts newest first."""
    page = _StubModelPage(
        data=[
            _StubModelEntry(id="gpt-4o", created=200),
            _StubModelEntry(id="gpt-4.1", created=300),
            _StubModelEntry(id="o3-mini", created=250),
            _StubModelEntry(id="text-embedding-3-large", created=400),
            _StubModelEntry(id="whisper-1", created=100),
            _StubModelEntry(id="dall-e-3", created=350),
            _StubModelEntry(id="gpt-4o-realtime-preview", created=360),
            _StubModelEntry(id="gpt-4o-audio-preview", created=370),
            _StubModelEntry(id="gpt-4o-transcribe", created=380),
            _StubModelEntry(id="omni-moderation-latest", created=390),
        ]
    )
    stub_client = _StubOpenAIClient(page)
    models = await list_chat_models("sk-test", client=cast(Any, stub_client))

    assert models == ["gpt-4.1", "o3-mini", "gpt-4o"]
    assert is_chat_model("gpt-4o") is True
    assert is_chat_model("gpt-4.1-mini") is True
    assert is_chat_model("o4-mini") is True
    assert is_chat_model("text-embedding-3-small") is False
    assert is_chat_model("gpt-4o-realtime-preview") is False
    assert is_chat_model("") is False


async def test_retrying_model_retries_429_twice_then_succeeds() -> None:
    """TC-09: Model call failing twice with 429 emits 2 retry statuses then succeeds."""
    err_429 = openai.RateLimitError(
        "Rate limit reached",
        response=cast(Any, _http_response(429)),
        body=None,
    )
    fake = FakeModel(
        turns=[
            FakeTurn.error(err_429),
            FakeTurn.error(err_429),
            FakeTurn.text(["Hello ", "world"]),
        ]
    )
    recorded_retries: list[tuple[int, int, type[BaseException]]] = []
    recorded_sleeps: list[float] = []

    async def on_retry(attempt: int, max_retries: int, exc: BaseException) -> None:
        recorded_retries.append((attempt, max_retries, type(exc)))

    async def fake_sleep(delay: float) -> None:
        recorded_sleeps.append(delay)

    model = RetryingModel(
        fake,
        max_retries=3,
        base_delay=1.0,
        jitter=0.2,
        on_retry=on_retry,
        sleep=fake_sleep,
    )

    deltas: list[Delta] = []
    async for delta in model.stream([ChatMessage(role="user", content="hi")]):
        deltas.append(delta)

    assert [d.content for d in deltas if d.content] == ["Hello ", "world"]
    assert len(fake.calls) == 3
    assert recorded_retries == [
        (1, 3, openai.RateLimitError),
        (2, 3, openai.RateLimitError),
    ]
    assert len(recorded_sleeps) == 2
    assert 0.8 <= recorded_sleeps[0] <= 1.2
    assert 1.6 <= recorded_sleeps[1] <= 2.4


async def test_retrying_model_exhausts_3_retries_on_503() -> None:
    """TC-10: Model failing 4 times (initial + 3 retries) with 503 raises on 4th."""
    err_503 = openai.InternalServerError(
        "Service unavailable",
        response=cast(Any, _http_response(503)),
        body=None,
    )
    fake = FakeModel(
        turns=[
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
        ]
    )
    retry_attempts: list[tuple[int, int]] = []
    sleeps: list[float] = []

    def on_retry(attempt: int, max_retries: int, _exc: BaseException) -> None:
        retry_attempts.append((attempt, max_retries))

    async def fake_sleep(delay: float) -> None:
        sleeps.append(delay)

    model = RetryingModel(
        fake,
        max_retries=3,
        base_delay=1.0,
        jitter=0.0,
        on_retry=on_retry,
        sleep=fake_sleep,
    )

    with pytest.raises(openai.InternalServerError):
        async for _ in model.stream([ChatMessage(role="user", content="hi")]):
            pass

    assert len(fake.calls) == 4
    assert retry_attempts == [(1, 3), (2, 3), (3, 3)]
    assert sleeps == [1.0, 2.0, 4.0]
    code, msg = classify_model_error(err_503)
    assert code == "model_unavailable"
    assert "connection" in msg.lower()


async def test_retrying_model_never_retries_auth_or_mid_stream_failure() -> None:
    """TC-18: Auth errors (401) and mid-stream failures after a chunk do not retry."""
    err_401 = openai.AuthenticationError(
        "Invalid key sk-proj-secretkeyvalue123456",
        response=cast(Any, _http_response(401)),
        body=None,
    )
    fake_auth = FakeModel(turns=[FakeTurn.error(err_401)])
    retries_called = 0

    def on_retry(_attempt: int, _max: int, _exc: BaseException) -> None:
        nonlocal retries_called
        retries_called += 1

    async def fake_sleep(_delay: float) -> None:
        pass

    model_auth = RetryingModel(fake_auth, on_retry=on_retry, sleep=fake_sleep)
    with pytest.raises(openai.AuthenticationError):
        await model_auth.generate([ChatMessage(role="user", content="hi")])

    assert retries_called == 0
    assert len(fake_auth.calls) == 1
    code, message = classify_model_error(err_401)
    assert code == "auth_failed"
    assert "sk-proj-secretkeyvalue123456" not in message

    # Mid-stream failure after yielding one Delta must not retry from scratch.
    class _MidStreamFailingModel(FakeModel):
        async def stream(self, messages: Any, tools: Any = None) -> Any:
            self.calls.append(list(messages))
            yield Delta(content="Part 1")
            raise TransientModelError("connection dropped mid-stream")

    mid_fake = _MidStreamFailingModel()
    model_mid = RetryingModel(mid_fake, on_retry=on_retry, sleep=fake_sleep)
    received: list[str] = []
    with pytest.raises(TransientModelError):
        async for delta in model_mid.stream([ChatMessage(role="user", content="hi")]):
            if delta.content:
                received.append(delta.content)

    assert received == ["Part 1"]
    assert retries_called == 0
    assert len(mid_fake.calls) == 1


def test_openai_model_disables_inner_sdk_retries() -> None:
    """TC-19: `build_openai_model` disables inner `OpenAIModel` and SDK retries."""
    wrapped = build_openai_model("gpt-4o", "sk-test-key-12345678")
    assert wrapped.max_retries == 3
    inner = cast(Any, wrapped.inner)
    assert inner.max_retries == 0
    assert inner.client.max_retries == 0
