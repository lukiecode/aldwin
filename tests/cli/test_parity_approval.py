"""Approval chooser: selection highlight, reason before Live resume."""

from __future__ import annotations

from io import StringIO

import pytest
from prompt_toolkit.input import create_pipe_input

from aldwin.cli.chat_screen import _read_approval_key_detail
from aldwin.cli.render import ChatRenderer, TurnRenderer
from aldwin.cli.theme import create_console
from aldwin.protocol import ApprovalEvent


def _approval(*, grantable: bool = False) -> ApprovalEvent:
    return ApprovalEvent(
        re="r1",
        tool="run_command",
        approval_id="ap_1",
        summary="Run ls",
        detail="ls -la",
        grantable=grantable,
        digest="d1",
    )


def test_resolve_approval_reason_before_resume() -> None:
    buf = StringIO()
    console = create_console(plain=False, width=80, file=buf)
    chat = ChatRenderer(console, plain=False)
    turn = TurnRenderer(chat)
    ev = _approval()
    turn.pause_for_approval(ev)
    turn.collapse_approval_panel()
    assert turn._approval_live is None
    turn.resolve_approval(ev, "no", reason="too risky", resume=False)
    out = buf.getvalue()
    assert "declined" in out
    assert "too risky" in out
    assert turn._live is None
    turn.resume_after_approval()
    assert turn._live is not None
    turn.close()


def test_set_approval_selection_updates_highlight() -> None:
    buf = StringIO()
    console = create_console(plain=False, width=80, file=buf)
    chat = ChatRenderer(console, plain=False)
    turn = TurnRenderer(chat)
    ev = _approval(grantable=True)
    turn.pause_for_approval(ev)
    turn.set_approval_selection(1)
    assert turn.approval_selected == 1
    panel = chat.build_approval_panel(ev, selected=1)
    out_buf = StringIO()
    rendered = create_console(plain=False, width=80, file=out_buf)
    rendered.print(panel)
    text = out_buf.getvalue()
    assert "Yes" in text
    turn.close()


def test_resolve_yes_session_and_plain_chooser() -> None:
    buf = StringIO()
    console = create_console(plain=True, width=80, file=buf)
    chat = ChatRenderer(console, plain=True)
    turn = TurnRenderer(chat)
    ev = _approval(grantable=True)
    turn.pause_for_approval(ev)
    turn.resolve_approval(ev, "yes_session", resume=False)
    out = buf.getvalue()
    assert "approved for session" in out or "session" in out
    panel = chat.build_approval_panel(ev, selected=2)
    out2 = StringIO()
    create_console(plain=True, width=80, file=out2).print(panel)
    assert "Yes" in out2.getvalue()
    turn.close()


def test_expand_approval_without_live() -> None:
    buf = StringIO()
    console = create_console(plain=False, width=80, file=buf)
    chat = ChatRenderer(console, plain=False)
    turn = TurnRenderer(chat)
    lines = "\n".join(f"+ line {i}" for i in range(30))
    ev = ApprovalEvent(
        re="r1",
        tool="edit_file",
        approval_id="ap_d",
        summary="Edit file",
        detail=lines,
        tier="diff",
        grantable=False,
        digest="d2",
    )
    turn.pause_for_approval(ev)
    turn.collapse_approval_panel()
    turn.expand_approval_panel(ev)
    turn.close()


@pytest.mark.asyncio
async def test_approval_arrows_and_enter_select_yes() -> None:
    import asyncio

    with create_pipe_input() as pipe:
        selections: list[int] = []
        task = asyncio.create_task(
            _read_approval_key_detail(
                pipe,
                grantable=False,
                on_select=selections.append,
            )
        )
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b[B")  # Down -> Yes
        pipe.send_text("\r")
        answer, explicit_n = await asyncio.wait_for(task, timeout=1.0)
        assert answer == "yes"
        assert explicit_n is False
        assert selections == [1]
