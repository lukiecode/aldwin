"""Inline pick_index chooser for /model and similar lists."""

from __future__ import annotations

import asyncio

import pytest
from prompt_toolkit.input import create_pipe_input

from aldwin.cli.picker import pick_index


@pytest.mark.asyncio
async def test_pick_index_enter_selects_default() -> None:
    with create_pipe_input() as pipe:
        task = asyncio.create_task(
            pick_index(["a", "b", "c"], default=1, input_dev=pipe, plain=True)
        )
        await asyncio.sleep(0.05)
        pipe.send_text("\r")
        idx = await asyncio.wait_for(task, timeout=1.0)
        assert idx == 1


@pytest.mark.asyncio
async def test_pick_index_escape_cancels() -> None:
    with create_pipe_input() as pipe:
        task = asyncio.create_task(
            pick_index(["a", "b"], default=0, input_dev=pipe, plain=True)
        )
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b")
        idx = await asyncio.wait_for(task, timeout=1.0)
        assert idx is None


@pytest.mark.asyncio
async def test_pick_index_digit_shortcut() -> None:
    with create_pipe_input() as pipe:
        task = asyncio.create_task(
            pick_index(["one", "two", "three"], default=0, input_dev=pipe, plain=True)
        )
        await asyncio.sleep(0.05)
        pipe.send_text("3")
        idx = await asyncio.wait_for(task, timeout=1.0)
        assert idx == 2
