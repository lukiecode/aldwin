"""Type-ahead queue while a turn runs (TurnKeyListener)."""

from __future__ import annotations

import asyncio

import pytest
from prompt_toolkit.input import create_pipe_input

from aldwin.cli.chat_screen import TurnKeyListener


@pytest.mark.asyncio
async def test_typeahead_enter_queues_draft() -> None:
    queued: list[str] = []

    async def on_escape() -> None:
        return None

    async def on_ctrl_c() -> None:
        return None

    async def on_queue(text: str) -> None:
        queued.append(text)

    with create_pipe_input() as pipe:
        async with TurnKeyListener(
            pipe,
            on_escape=on_escape,
            on_ctrl_c=on_ctrl_c,
            on_queue=on_queue,
        ):
            pipe.send_text("hello next\r")
            await asyncio.sleep(0.15)
        assert queued == ["hello next"]


@pytest.mark.asyncio
async def test_typeahead_leftover_draft_without_enter() -> None:
    async def _noop() -> None:
        return None

    async def _noop_queue(_text: str) -> None:
        return None

    with create_pipe_input() as pipe:
        async with TurnKeyListener(
            pipe,
            on_escape=_noop,
            on_ctrl_c=_noop,
            on_queue=_noop_queue,
        ) as listener:
            pipe.send_text("partial")
            await asyncio.sleep(0.15)
            assert listener.draft == "partial"
