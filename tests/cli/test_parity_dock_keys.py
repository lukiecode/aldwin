"""Drive dock key bindings (paste collapse, newline, Ctrl-C clear) via PipeInput."""

from __future__ import annotations

import asyncio

import pytest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from aldwin.cli.dock import AldwinPromptSession
from aldwin.cli.paste import PasteStore


@pytest.mark.asyncio
async def test_dock_bracketed_paste_collapses_large_paste() -> None:
    store = PasteStore()
    with create_pipe_input() as pipe:
        session = AldwinPromptSession(
            input=pipe,
            output=DummyOutput(),
            paste_store=store,
        )
        session.key_bindings = session.attach_key_bindings()
        body = "one\ntwo\nthree\nfour"
        # Bracketed paste sequence.
        pipe.send_text(f"\x1b[200~{body}\x1b[201~")
        pipe.send_text("\r")

        result = await asyncio.wait_for(
            session.prompt_async("> "),
            timeout=2.0,
        )
        assert "Pasted text #" in result
        assert store.resolve(result) == body


@pytest.mark.asyncio
async def test_dock_ctrl_j_inserts_newline_then_submit() -> None:
    with create_pipe_input() as pipe:
        session = AldwinPromptSession(input=pipe, output=DummyOutput())
        session.key_bindings = session.attach_key_bindings()
        pipe.send_text("hello")
        pipe.send_text("\x0a")  # Ctrl-J -> newline
        pipe.send_text("world\r")
        result = await asyncio.wait_for(session.prompt_async("> "), timeout=2.0)
        assert "hello\nworld" in result or result == "hello\nworld"


@pytest.mark.asyncio
async def test_dock_double_esc_clears_draft() -> None:
    with create_pipe_input() as pipe:
        session = AldwinPromptSession(input=pipe, output=DummyOutput())
        session.key_bindings = session.attach_key_bindings()
        pipe.send_text("draft")
        pipe.send_text("\x1b")
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b")
        await asyncio.sleep(0.05)
        pipe.send_text("ok\r")
        result = await asyncio.wait_for(session.prompt_async("> "), timeout=2.0)
        assert "ok" in result


@pytest.mark.asyncio
async def test_dock_ctrl_c_clears_draft_then_eof_quits() -> None:
    quits: list[bool] = []

    def on_quit() -> None:
        quits.append(True)

    with create_pipe_input() as pipe:
        session = AldwinPromptSession(
            input=pipe,
            output=DummyOutput(),
            on_quit=on_quit,
        )
        session.key_bindings = session.attach_key_bindings()
        pipe.send_text("draft")
        pipe.send_text("\x03")  # clear draft
        pipe.send_text("\x03")  # first empty ctrl-c
        pipe.send_text("\x03")  # second empty ctrl-c -> quit
        with pytest.raises(EOFError):
            await asyncio.wait_for(session.prompt_async("> "), timeout=2.0)
        assert quits == [True]
