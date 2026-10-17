"""Integration tests for `AldwinClient` -> `AldwinHost` -> `aldwin.core`."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import httpx
import openai
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.home import ensure_home
from aldwin.core.models import RetryingModel
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host import AldwinHost, AskFn, InProcessTransport
from aldwin.protocol import DoneEvent, ErrorEvent, Event, StatusEvent, TextEvent


def _http_response(status_code: int) -> httpx.Response:
    req = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    return httpx.Response(status_code, request=req)


async def _collect(client: AldwinClient, text: str) -> list[Event]:
    events: list[Event] = []
    async for ev in client.turn(text):
        events.append(ev)
    return events


async def test_host_emits_retry_status_events_then_answers(
    aldwin_home: Path,
) -> None:
    """TC-09: Two 429 errors emit two `StatusEvent`s before the streamed answer."""
    ensure_home(aldwin_home)
    err_429 = openai.RateLimitError(
        "Rate limit reached",
        response=cast(Any, _http_response(429)),
        body=None,
    )
    fake_model = FakeModel(
        turns=[
            FakeTurn.error(err_429),
            FakeTurn.error(err_429),
            FakeTurn.text(["Recovered answer."]),
        ]
    )

    async def zero_sleep(_delay: float) -> None:
        return None

    def builder(model_name: str, ask: AskFn) -> Any:
        retrying = RetryingModel(fake_model, max_retries=3, sleep=zero_sleep)
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=retrying,
        )

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder)
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect(client, "Hello")

        statuses = [ev.message for ev in events if isinstance(ev, StatusEvent)]
        assert statuses == [
            "OpenAI is busy, retrying (1 of 3)…",
            "OpenAI is busy, retrying (2 of 3)…",
        ]
        texts = [ev.delta for ev in events if isinstance(ev, TextEvent)]
        assert texts == ["Recovered answer."]
        assert isinstance(events[-1], DoneEvent)
        assert events[-1].reason == "answered"
    finally:
        await client.close()
        await host.stop_host()


async def test_host_emits_3_retry_statuses_then_model_unavailable_on_503(
    aldwin_home: Path,
) -> None:
    """TC-10: Three failed retries on 503 emit 3 `StatusEvent`s then `ErrorEvent`."""
    ensure_home(aldwin_home)
    err_503 = openai.InternalServerError(
        "Service unavailable",
        response=cast(Any, _http_response(503)),
        body=None,
    )
    fake_model = FakeModel(
        turns=[
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
            FakeTurn.error(err_503),
        ]
    )

    async def zero_sleep(_delay: float) -> None:
        return None

    def builder(model_name: str, ask: AskFn) -> Any:
        retrying = RetryingModel(fake_model, max_retries=3, sleep=zero_sleep)
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=retrying,
        )

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder)
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        events = await _collect(client, "Hello")

        statuses = [ev.message for ev in events if isinstance(ev, StatusEvent)]
        assert statuses == [
            "OpenAI is busy, retrying (1 of 3)…",
            "OpenAI is busy, retrying (2 of 3)…",
            "OpenAI is busy, retrying (3 of 3)…",
        ]
        assert isinstance(events[-1], ErrorEvent)
        assert events[-1].code == "model_unavailable"
    finally:
        await client.close()
        await host.stop_host()


async def test_host_core_context_and_model_switch_end_to_end(
    aldwin_home: Path,
) -> None:
    """TC-11: End-to-end `AldwinHost` + `build_agent` switches model cleanly."""
    ensure_home(aldwin_home)
    models_by_name: dict[str, FakeModel] = {
        "gpt-4o": FakeModel(turns=[FakeTurn.text(["Hello Fola from 4o"])]),
        "gpt-4.1": FakeModel(turns=[FakeTurn.text(["Hello Fola from 4.1"])]),
    }

    def builder(model_name: str, ask: AskFn) -> Any:
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=models_by_name[model_name],
        )

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=builder,
        initial_model="gpt-4o",
        available_models=["gpt-4o", "gpt-4.1"],
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        await _collect(client, "My name is Fola")

        res = await client.set_model("gpt-4.1")
        assert isinstance(res, DoneEvent) and res.reason == "ok"

        (aldwin_home / "persona.md").write_text("Be concise.\n", encoding="utf-8")
        await _collect(client, "What is my name?")

        sent = models_by_name["gpt-4.1"].received_messages
        assert sent[0].role == "system"
        assert "## Persona\nBe concise." in sent[0].content
        assert sent[0].content.count("Available skills:") == 1
        assert [(m.role, m.content) for m in sent[1:]] == [
            ("user", "My name is Fola"),
            ("assistant", "Hello Fola from 4o"),
            ("user", "What is my name?"),
        ]
    finally:
        await client.close()
        await host.stop_host()
