"""Agentling tools for `remember` and `recall` (`ENG-381`, `FR-3`, `FR-4`)."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path
from typing import Any

from agentling import Tool

from aldwin.core.memory import DEFAULT_RECALL_DAYS, recall_notes, remember_note


class RememberTool(Tool):
    """Append a short, durable fact to today's notes file (`FR-3`).

    Cannot write `profile.md` or `persona.md`; accepts only `text`.
    """

    name = "remember"
    description = (
        "Save a short, durable fact about the user or their preferences to today's "
        "memory notes (1-300 characters, single line). Never store secrets or API keys."
    )
    parallel_safe = False
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": (
                    "Single-line fact to remember (1-300 characters). "
                    "Never pass API keys, passwords, or secrets."
                ),
            },
        },
        "required": ["text"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        home: Path | None = None,
        *,
        session_id_getter: Callable[[], str] | None = None,
    ) -> None:
        self._home = home
        self._session_id_getter = session_id_getter

    async def forward(self, text: str) -> str:  # type: ignore[override]
        session_id = (
            self._session_id_getter()
            if self._session_id_getter is not None
            else "default"
        )
        try:
            return await asyncio.to_thread(
                remember_note,
                self._home,
                text,
                session_id=session_id,
            )
        except ValueError as exc:
            return str(exc)


class RecallTool(Tool):
    """Search recent daily notes by matching all words in `query` (`FR-4`)."""

    name = "recall"
    description = (
        "Search saved memory notes from recent days by matching all words in a query."
    )
    parallel_safe = True
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Words to match against saved memory notes.",
            },
            "days": {
                "type": "integer",
                "description": "Number of recent days to search (1-92, default 30).",
                "default": DEFAULT_RECALL_DAYS,
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, home: Path | None = None) -> None:
        self._home = home

    async def forward(  # type: ignore[override]
        self,
        query: str,
        days: int = DEFAULT_RECALL_DAYS,
    ) -> str:
        return await asyncio.to_thread(
            recall_notes,
            self._home,
            query,
            days,
        )


def build_memory_tools(
    home: Path | None = None,
    *,
    session_id_getter: Callable[[], str] | None = None,
) -> tuple[RememberTool, RecallTool]:
    """Construct the `(RememberTool, RecallTool)` pair for `home`."""
    return (
        RememberTool(home=home, session_id_getter=session_id_getter),
        RecallTool(home=home),
    )
