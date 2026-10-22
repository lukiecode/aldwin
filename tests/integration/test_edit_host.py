"""Host integration tests for file edits."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Literal

import pytest
from fakes.model import FakeModel, FakeTurn

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent
from aldwin.host import AldwinHost, InProcessTransport
from aldwin.protocol import ApprovalEvent, ToolCallEvent, ToolResultEvent

EditHost = tuple[AldwinClient, FakeModel, Path, AldwinHost]
ApproveAnswer = Literal["yes", "yes_session", "no"]


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
async def edit_host(
    fake_model: FakeModel,
    aldwin_home: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[EditHost]:
    """In-process host with a temp workspace and the fake model."""
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
        yield client, fake_model, root, host
    finally:
        await client.close()
        await host.stop_host()


async def test_two_edits_one_step_decline_second(edit_host: EditHost) -> None:
    """Two `edit_file` calls in one step: approvals are sequential; a no denies both."""
    client, model, root, host = edit_host
    _write(root / "a.md", "alpha\n")
    _write(root / "b.md", "beta\n")
    # Read-before-write must already be satisfied before the Diff step.
    host.policy_session.mark_read(root / "a.md")
    host.policy_session.mark_read(root / "b.md")

    model.add_turns(
        FakeTurn(
            tool_calls=[
                (
                    "c_e1",
                    "edit_file",
                    {"path": "a.md", "old": "alpha", "new": "ALPHA"},
                ),
                (
                    "c_e2",
                    "edit_file",
                    {"path": "b.md", "old": "beta", "new": "BETA"},
                ),
            ]
        ),
        FakeTurn.text(["Stopped after decline."]),
    )

    approvals: list[ApprovalEvent] = []
    events: list[object] = []
    pending: str | None = None
    async for event in client.turn("fix both typos"):
        events.append(event)
        if isinstance(event, ApprovalEvent):
            assert pending is None, "two approvals pending at once"
            pending = event.approval_id
            approvals.append(event)
            answer: ApproveAnswer = "yes" if len(approvals) == 1 else "no"
            await client.approve(event.approval_id, answer, digest=event.digest)
            pending = None

    assert len(approvals) == 2
    assert approvals[0].tool == "edit_file"
    assert approvals[1].tool == "edit_file"
    assert (root / "a.md").read_text(encoding="utf-8") == "alpha\n"
    assert (root / "b.md").read_text(encoding="utf-8") == "beta\n"

    results = [event for event in events if isinstance(event, ToolResultEvent)]
    assert len(results) == 2
    assert all(not result.ok for result in results)
    assert all("declined" in (result.summary or "") for result in results)

    calls = [event for event in events if isinstance(event, ToolCallEvent)]
    assert [event.summary for event in calls] == ["Edit a.md", "Edit b.md"]


async def test_diff_approvals_never_overlap(edit_host: EditHost) -> None:
    """A second Diff approval is only emitted after the first is answered."""
    client, model, root, host = edit_host
    _write(root / "a.md", "one\n")
    _write(root / "b.md", "two\n")
    host.policy_session.mark_read(root / "a.md")
    host.policy_session.mark_read(root / "b.md")

    model.add_turns(
        FakeTurn(
            tool_calls=[
                ("c1", "edit_file", {"path": "a.md", "old": "one", "new": "ONE"}),
                ("c2", "edit_file", {"path": "b.md", "old": "two", "new": "TWO"}),
            ]
        ),
        FakeTurn.text(["done"]),
    )

    pending: str | None = None
    ask_summaries: list[str] = []
    async for event in client.turn("edit both"):
        if isinstance(event, ApprovalEvent):
            assert pending is None, "two approvals pending at once"
            pending = event.approval_id
            ask_summaries.append(event.summary)
            await client.approve(event.approval_id, "yes", digest=event.digest)
            pending = None

    assert len(ask_summaries) == 2
    assert ask_summaries[0].startswith("Edit a.md")
    assert ask_summaries[1].startswith("Edit b.md")
    assert (root / "a.md").read_text(encoding="utf-8") == "ONE\n"
    assert (root / "b.md").read_text(encoding="utf-8") == "TWO\n"
