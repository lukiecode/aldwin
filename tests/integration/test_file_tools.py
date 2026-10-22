"""Host integration tests for read-only file tools."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from conftest import auto_approve
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.core.tools import ToolOutput
from aldwin.core.tools.files import GrepTool, ReadFileTool
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import ToolCallEvent, ToolResultEvent


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
async def file_host(
    fake_model: FakeModel,
    aldwin_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """An in-process host whose workspace is a temp folder and whose model is fake."""
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
        yield client, fake_model, root
    finally:
        await client.close()
        await host.stop_host()


async def test_tc13_grep_and_read_file_concurrent(
    file_host: tuple[AldwinClient, FakeModel, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """grep and read_file in one step both start before either finishes."""
    client, model, root = file_host
    _write(root / "a.txt", "stop\n")
    grep_started = asyncio.Event()
    read_started = asyncio.Event()
    original_grep = GrepTool.forward
    original_read = ReadFileTool.forward

    async def grep_forward(
        self: GrepTool,
        pattern: str,
        path: str = ".",
        glob: str | None = None,
        ignore_case: bool = False,
        include_ignored: bool = False,
    ) -> ToolOutput:
        grep_started.set()
        await asyncio.wait_for(read_started.wait(), 1)
        return await original_grep(
            self,
            pattern,
            path,
            glob,
            ignore_case,
            include_ignored,
        )

    async def read_forward(
        self: ReadFileTool,
        path: str,
        offset: int = 1,
        limit: int = 2000,
    ) -> ToolOutput:
        read_started.set()
        await asyncio.wait_for(grep_started.wait(), 1)
        return await original_read(self, path, offset, limit)

    monkeypatch.setattr(GrepTool, "forward", grep_forward)
    monkeypatch.setattr(ReadFileTool, "forward", read_forward)
    model.add_turns(
        FakeTurn(
            tool_calls=[
                ("c_grep", "grep", {"pattern": "stop"}),
                ("c_read", "read_file", {"path": "a.txt"}),
            ]
        )
    )
    events = await auto_approve(client, client.turn("go"))
    calls = [event for event in events if isinstance(event, ToolCallEvent)]
    results = [event for event in events if isinstance(event, ToolResultEvent)]
    assert not any(event.kind == "approval" for event in events)
    assert [event.summary for event in calls] == ['Search "stop"', "Read a.txt"]
    assert [event.summary for event in results] == [
        'Search "stop" · 1 match in 1 file',
        "Read a.txt · lines 1–1 of 1",
    ]
    assert grep_started.is_set()
    assert read_started.is_set()


async def test_tc42_host_rows_and_preview(
    file_host: tuple[AldwinClient, FakeModel, Path],
) -> None:
    """A successful read carries the running row, the result sentence, and 12 lines."""
    client, model, root = file_host
    _write(root / "a.txt", "".join("row\n" for _ in range(15)))
    model.add_turns(FakeTurn(tool_calls=[("c1", "read_file", {"path": "a.txt"})]))
    events = await auto_approve(client, client.turn("go"))
    call = next(event for event in events if isinstance(event, ToolCallEvent))
    result = next(event for event in events if isinstance(event, ToolResultEvent))
    assert call.summary == "Read a.txt"
    assert result.ok is True
    assert result.summary == "Read a.txt · lines 1–15 of 15"
    preview_lines = result.preview.splitlines()
    assert preview_lines == [f"{number}\trow" for number in range(1, 13)]
    assert "<untrusted" not in result.preview
    assert "13\t" not in result.preview


async def test_tc43_host_denial_rows(
    file_host: tuple[AldwinClient, FakeModel, Path],
) -> None:
    """Denied file tools use the short row sentence and do not leak the file."""
    client, model, root = file_host
    _write(root / ".env", "SECRET\n")
    outside = root.parent / "note.txt"
    outside.write_text("SECRET\n", encoding="utf-8")

    async def one_turn(call_id: str, name: str, arguments: dict[str, str]) -> list:
        model.add_turns(
            FakeTurn(tool_calls=[(call_id, name, arguments)]),
            FakeTurn.text("done"),
        )
        return await auto_approve(client, client.turn(call_id), "no")

    events = []
    events.extend(await one_turn("c_env", "read_file", {"path": ".env"}))
    events.extend(await one_turn("c_glob", "glob", {"pattern": "../**/*.md"}))
    events.extend(await one_turn("c_out", "read_file", {"path": str(outside)}))
    results = [event for event in events if isinstance(event, ToolResultEvent)]
    approvals = [event for event in events if event.kind == "approval"]
    assert all(".env" not in event.summary for event in approvals)
    by_summary = {event.summary: event for event in results}
    assert by_summary["Read .env · blocked: may contain secrets"].ok is False
    assert (
        "Find ../**/*.md · blocked: pattern must stay inside the search folder"
        in by_summary
    )
    outside_results = [
        event
        for event in results
        if event.summary.startswith("Read ") and event.summary.endswith(" · declined")
    ]
    assert len(outside_results) == 1
    for event in results:
        assert "SECRET" not in event.summary
        assert "SECRET" not in event.preview
