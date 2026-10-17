"""Tests for `aldwin.client.AldwinClient` (`FR-16`)."""

from __future__ import annotations

from pathlib import Path

from agentling import Agent
from agentling.memory import ActionStep, TaskStep, ToolResult
from agentling.models import ChatMessage, ToolCall
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.home import ensure_home
from aldwin.host import AldwinHost, InProcessTransport, SessionStore
from aldwin.protocol import DoneEvent, Event, HistoryItem, TextEvent


async def test_client_history_and_new_conversation_lifecycle(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """TC-12, TC-26, TC-28: `history`, `new_conversation`, and idle `stop`."""
    ensure_home(aldwin_home)
    fake_model.add_turns(
        FakeTurn.text(["First reply."]),
        FakeTurn.text(["Second reply."]),
        FakeTurn.text(["Fresh session reply."]),
    )
    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()

        idle_stop = await client.stop()
        assert isinstance(idle_stop, DoneEvent)
        assert idle_stop.reason == "ok"

        async for _ev in client.turn("Hello 1"):
            pass
        async for _ev in client.turn("Hello 2"):
            pass

        first_session_path = host.stored_session.path
        hist = await client.history(limit=20)
        assert hist.interrupted is False
        assert hist.messages == [
            HistoryItem(role="user", text="Hello 1"),
            HistoryItem(role="assistant", text="First reply."),
            HistoryItem(role="user", text="Hello 2"),
            HistoryItem(role="assistant", text="Second reply."),
        ]

        await client.new_conversation()
        assert host.stored_session.path != first_session_path
        assert first_session_path.exists()

        fresh_hist = await client.history(limit=20)
        assert fresh_hist.messages == []
        assert fresh_hist.interrupted is False

        async for _ev in client.turn("Hello fresh"):
            pass
        updated_hist = await client.history(limit=20)
        assert updated_hist.messages == [
            HistoryItem(role="user", text="Hello fresh"),
            HistoryItem(role="assistant", text="Fresh session reply."),
        ]
    finally:
        await client.close()
        await host.stop_host()


async def test_client_resumes_interrupted_session_on_host_restart(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """TC-11: Host restart loads interrupted session and reports `interrupted=True`."""
    ensure_home(aldwin_home)
    store = SessionStore()
    stored = store.create_new(model="gpt-4o")
    stored.memory.add(TaskStep(task="Cut off mid-work"))
    stored.memory.add(
        ActionStep(
            model_message=ChatMessage(
                role="assistant",
                content="",
                tool_calls=[
                    ToolCall(id="c1", name="web_search", arguments={"query": "python"})
                ],
            ),
            tool_results=[
                ToolResult(
                    tool_call_id="c1",
                    name="web_search",
                    content="5 results\n1. Python.org",
                )
            ],
        )
    )
    store.save(stored, stored.memory, interrupted=True)

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        hist = await client.history(limit=20)
        assert hist.interrupted is True
        assert hist.messages == [
            HistoryItem(role="user", text="Cut off mid-work"),
            HistoryItem(role="tool", text="✓ web_search · 5 results"),
        ]
    finally:
        await client.close()
        await host.stop_host()


async def test_client_cleans_up_queue_when_caller_breaks_early(
    aldwin_home: Path,
    fake_model: FakeModel,
) -> None:
    """TC-29: Breaking out of `client.turn()` early cleans up the per-request queue."""
    ensure_home(aldwin_home)
    fake_model.add_turns(FakeTurn.text(["chunk1", "chunk2", "chunk3"]))

    transport = InProcessTransport()
    host = AldwinHost(
        transport,
        agent_builder=lambda _m, _ask: Agent(model=fake_model),
    )
    await host.start()
    client = AldwinClient(transport.connect())

    try:
        await client.connect()
        seen: list[Event] = []
        async for ev in client.turn("Stream three chunks"):
            seen.append(ev)
            if isinstance(ev, TextEvent):
                break

        assert len(seen) == 1
        await client.stop()
        assert client._queues == {}

        hist = await client.history(limit=5)
        assert len(hist.messages) >= 1
    finally:
        await client.close()
        await host.stop_host()
