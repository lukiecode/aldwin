"""Session checklist tool for multi-step jobs (`todo`)."""

from __future__ import annotations

from typing import Any, Final, Literal

from agentling import Tool
from agentling.tools import ToolCallError

from aldwin.core.tools import ToolOutput

TodoStatus = Literal["pending", "in_progress", "done", "dropped"]

MAX_ITEMS: Final[int] = 20
MAX_TEXT_CHARS: Final[int] = 120
VALID_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "in_progress", "done", "dropped"}
)

STATUS_MARKERS: Final[dict[str, str]] = {
    "done": "[x]",
    "in_progress": "[>]",
    "pending": "[ ]",
    "dropped": "[-]",
}

TODO_DESCRIPTION: Final[str] = (
    "Keep a checklist for the current job. Use it only when the job has three "
    "or more steps; skip it for simple questions. Send the whole list each time. "
    "Keep exactly one item in progress while working. Mark an item done as soon "
    "as it is finished, before starting the next. Drop items you no longer need "
    "instead of deleting them. Keep item text short."
)


def todo_counts(items: list[dict[str, Any]]) -> tuple[int, int]:
    """Return ``(done_including_dropped, total)``."""
    done = sum(1 for item in items if item.get("status") in ("done", "dropped"))
    return done, len(items)


def format_todo_echo(items: list[dict[str, Any]]) -> str:
    """Plain-text echo for the model: ``Tasks n/m`` plus marker lines."""
    done, total = todo_counts(items)
    lines = [f"Tasks {done}/{total}"]
    for item in items:
        marker = STATUS_MARKERS.get(str(item.get("status")), "[ ]")
        text = str(item.get("text", ""))
        lines.append(f"{marker} {text}")
    return "\n".join(lines)


def validate_todo_items(items: Any) -> list[dict[str, str]]:
    """Validate and normalise a todo list; raise ``ToolCallError`` on failure."""
    if not isinstance(items, list):
        raise ToolCallError("items is empty")
    if len(items) == 0:
        raise ToolCallError("items is empty")
    if len(items) > MAX_ITEMS:
        raise ToolCallError(f"too many items (limit {MAX_ITEMS})")

    normalised: list[dict[str, str]] = []
    in_progress = 0
    for index, raw in enumerate(items, start=1):
        if not isinstance(raw, dict):
            raise ToolCallError(f"item {index}: invalid item")
        text = raw.get("text")
        status = raw.get("status", "pending")
        if not isinstance(text, str) or not text or len(text) > MAX_TEXT_CHARS:
            raise ToolCallError(
                f"item {index}: text must be 1-{MAX_TEXT_CHARS} characters"
            )
        if not isinstance(status, str) or status not in VALID_STATUSES:
            raise ToolCallError(
                f"item {index}: status must be pending, in_progress, done or dropped"
            )
        if status == "in_progress":
            in_progress += 1
        normalised.append({"text": text, "status": status})

    if in_progress > 1:
        raise ToolCallError("only one item can be in progress")
    return normalised


class TodoTool(Tool):
    """Replace the session checklist with the given items."""

    name = "todo"
    description = TODO_DESCRIPTION
    parallel_safe = False
    timeout = 5
    max_output_chars = 2_000
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "description": "Full checklist; replaces the previous list.",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": "Short task text (1-120 characters).",
                        },
                        "status": {
                            "type": "string",
                            "enum": [
                                "pending",
                                "in_progress",
                                "done",
                                "dropped",
                            ],
                            "description": "Task status.",
                        },
                    },
                    "required": ["text", "status"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["items"],
        "additionalProperties": False,
    }

    async def forward(  # type: ignore[override]
        self,
        items: list[dict[str, str]],
    ) -> ToolOutput:
        normalised = validate_todo_items(items)
        echo = format_todo_echo(normalised)
        done, total = todo_counts(normalised)
        return ToolOutput(
            echo,
            summary=f"Tasks {done}/{total}",
            ok=True,
        )


def build_todo_tool() -> TodoTool:
    """Construct the `todo` tool."""
    return TodoTool()


__all__ = [
    "TODO_DESCRIPTION",
    "TodoTool",
    "build_todo_tool",
    "format_todo_echo",
    "todo_counts",
    "validate_todo_items",
]
