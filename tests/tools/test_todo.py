"""Unit tests for the session checklist tool (`todo`)."""

from __future__ import annotations

import pytest
from agentling.tools import ToolCallError

from aldwin.core.tools.todo import (
    TODO_DESCRIPTION,
    TodoTool,
    format_todo_echo,
    todo_counts,
    validate_todo_items,
)


def _items(*pairs: tuple[str, str]) -> list[dict[str, str]]:
    return [{"text": text, "status": status} for text, status in pairs]


@pytest.mark.asyncio
async def test_todo_five_items_echo_tasks_0_of_5() -> None:
    tool = TodoTool()
    items = _items(
        ("List the Downloads folder", "in_progress"),
        ("Group files by type", "pending"),
        ("Rename the invoices", "pending"),
        ("Summarise the three contracts", "pending"),
        ("Report what changed", "pending"),
    )
    out = await tool.forward(items)
    assert str(out).startswith("Tasks 0/5\n")
    assert "[>] List the Downloads folder" in str(out)
    assert out.summary == "Tasks 0/5"
    assert out.ok is True


@pytest.mark.asyncio
async def test_todo_rejects_two_in_progress() -> None:
    tool = TodoTool()
    with pytest.raises(ToolCallError, match="only one item can be in progress"):
        await tool.forward(
            _items(("A", "in_progress"), ("B", "in_progress")),
        )


@pytest.mark.asyncio
async def test_todo_rejects_21_items() -> None:
    tool = TodoTool()
    items = _items(*((f"t{i}", "pending") for i in range(21)))
    with pytest.raises(ToolCallError, match=r"too many items \(limit 20\)"):
        await tool.forward(items)


@pytest.mark.asyncio
async def test_todo_rejects_empty_list() -> None:
    tool = TodoTool()
    with pytest.raises(ToolCallError, match="items is empty"):
        await tool.forward([])


@pytest.mark.asyncio
async def test_todo_rejects_bad_text() -> None:
    tool = TodoTool()
    with pytest.raises(
        ToolCallError,
        match=r"item 1: text must be 1-120 characters",
    ):
        await tool.forward([{"text": "", "status": "pending"}])
    with pytest.raises(
        ToolCallError,
        match=r"item 1: text must be 1-120 characters",
    ):
        await tool.forward([{"text": "x" * 121, "status": "pending"}])


@pytest.mark.asyncio
async def test_todo_rejects_non_dict_item() -> None:
    tool = TodoTool()
    with pytest.raises(ToolCallError, match=r"item 1: invalid item"):
        await tool.forward(["not a dict"])  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_todo_rejects_unknown_status() -> None:
    tool = TodoTool()
    with pytest.raises(
        ToolCallError,
        match=(
            r"item 1: status must be pending, in_progress, done or dropped"
        ),
    ):
        await tool.forward([{"text": "A", "status": "running"}])


@pytest.mark.asyncio
async def test_todo_boundary_20_items_120_chars() -> None:
    tool = TodoTool()
    items = [{"text": "x" * 120, "status": "pending"} for _ in range(20)]
    out = await tool.forward(items)
    assert out.summary == "Tasks 0/20"


def test_todo_echo_markers_and_done_count() -> None:
    items = _items(
        ("A", "done"),
        ("B", "done"),
        ("C", "dropped"),
        ("D", "in_progress"),
        ("E", "pending"),
    )
    echo = format_todo_echo(items)
    assert echo.startswith("Tasks 3/5\n")
    assert "[x] A" in echo
    assert "[-] C" in echo
    assert "[>] D" in echo
    assert "[ ] E" in echo
    assert todo_counts(items) == (3, 5)


def test_todo_description_exact() -> None:
    assert TodoTool.description == TODO_DESCRIPTION
    assert len(TODO_DESCRIPTION) <= 400


def test_todo_tool_max_output_chars() -> None:
    assert TodoTool.max_output_chars == 2_000
    items = [{"text": "x" * 120, "status": "pending"} for _ in range(20)]
    # Worst-case echo may exceed the cap; policy trim enforces max_output_chars.
    echo = format_todo_echo(validate_todo_items(items))
    assert len(echo) > 0
