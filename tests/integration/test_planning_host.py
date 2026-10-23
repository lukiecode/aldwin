"""Host integration tests for `todo` and `calculate`."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from conftest import collect
from fakes.model import FakeModel, final, tool_call

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    TodoUpdatedEvent,
    ToolResultEvent,
)


@pytest.fixture
async def planning_host(
    fake_model: FakeModel,
    aldwin_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """In-process host with fake model and a temp workspace."""
    root = tmp_path / "ws"
    root.mkdir()
    real_build = build_agent

    def _build(config, secrets, ask, **kwargs):
        kwargs["model"] = fake_model
        return real_build(config, secrets, ask, **kwargs)

    monkeypatch.setattr("aldwin.host.runner.build_agent", _build)
    transport = InProcessTransport()
    host = AldwinHost(transport, home=aldwin_home, workspace=root)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        yield client, fake_model, host, aldwin_home
    finally:
        await client.close()
        await host.stop_host()


async def test_todo_emits_updated_event(
    planning_host: tuple[AldwinClient, FakeModel, AldwinHost, Path],
) -> None:
    client, model, _host, _home = planning_host
    items = [
        {"text": "List downloads", "status": "in_progress"},
        {"text": "Rename invoices", "status": "pending"},
        {"text": "Summarise", "status": "pending"},
    ]
    model.add_turns(
        tool_call("todo", {"items": items}),
        final("On it."),
    )
    events = await collect(client.turn("plan the job"))

    assert not any(isinstance(ev, ApprovalEvent) for ev in events)
    updated = [ev for ev in events if isinstance(ev, TodoUpdatedEvent)]
    assert len(updated) == 1
    assert updated[0].total == 3
    assert updated[0].done == 0
    assert updated[0].items[0].status == "in_progress"

    results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
    assert any(ev.summary == "Tasks 0/3" for ev in results)
    assert isinstance(events[-1], DoneEvent)


async def test_todo_persists_across_resume(
    planning_host: tuple[AldwinClient, FakeModel, AldwinHost, Path],
) -> None:
    client, model, host, home = planning_host
    items = [
        {"text": "A", "status": "done"},
        {"text": "B", "status": "in_progress"},
    ]
    model.add_turns(tool_call("todo", {"items": items}), final("ok"))
    await collect(client.turn("start"))

    todo_ev = await client.todo_get()
    assert todo_ev.total == 2
    assert todo_ev.done == 1

    session_files = list((home / "sessions").glob("*.json"))
    assert session_files
    payload = json.loads(session_files[0].read_text(encoding="utf-8"))
    assert payload["todo"][0]["text"] == "A"


async def test_new_clears_todo(
    planning_host: tuple[AldwinClient, FakeModel, AldwinHost, Path],
) -> None:
    client, model, _host, _home = planning_host
    model.add_turns(
        tool_call(
            "todo",
            {"items": [{"text": "A", "status": "pending"}]},
        ),
        final("ok"),
    )
    await collect(client.turn("start"))
    await client.new_conversation()
    todo_ev = await client.todo_get()
    assert todo_ev.total == 0
    assert todo_ev.items == []


async def test_failed_todo_does_not_mutate(
    planning_host: tuple[AldwinClient, FakeModel, AldwinHost, Path],
) -> None:
    client, model, _host, _home = planning_host
    model.add_turns(
        tool_call(
            "todo",
            {
                "items": [
                    {"text": "A", "status": "in_progress"},
                    {"text": "B", "status": "pending"},
                ]
            },
        ),
        final("ok"),
    )
    await collect(client.turn("first"))

    model.add_turns(
        tool_call(
            "todo",
            {
                "items": [
                    {"text": "A", "status": "in_progress"},
                    {"text": "B", "status": "in_progress"},
                ]
            },
        ),
        final("failed"),
    )
    events = await collect(client.turn("bad"))
    assert not any(isinstance(ev, TodoUpdatedEvent) for ev in events)
    todo_ev = await client.todo_get()
    assert todo_ev.total == 2
    assert todo_ev.items[0].status == "in_progress"


async def test_calculate_no_approval_no_untrusted(
    planning_host: tuple[AldwinClient, FakeModel, AldwinHost, Path],
) -> None:
    client, model, host, home = planning_host
    model.add_turns(
        tool_call("calculate", {"expression": "15% of 1240"}),
        final("£186"),
    )
    events = await collect(client.turn("what is 15% of 1240?"))

    assert not any(isinstance(ev, ApprovalEvent) for ev in events)
    results = [ev for ev in events if isinstance(ev, ToolResultEvent)]
    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].summary.startswith("Calculate ")
    assert "186" in results[0].summary
    assert "<untrusted" not in (results[0].preview or "")

    log_path = home / "logs" / "approvals.jsonl"
    assert log_path.is_file()
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    assert any(
        '"tool": "calculate"' in line
        or '"name": "calculate"' in line
        or "calculate" in line
        for line in lines
    )
