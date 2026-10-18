"""Agentling tool for updating `persona.md` after user `y/n` approval (`FR-8`)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from agentling import Tool

from aldwin.core.persona import DEFAULT_PERSONA_CAP, write_persona
from aldwin.core.tools import ToolOutput

AskFn = Callable[[str, str], Awaitable[bool]]


class SetPersonaTool(Tool):
    """Replace `persona.md` only after explicit `y/n` approval via `ask()` (`FR-8`)."""

    name = "set_persona"
    description = (
        "Propose a new persona for Aldwin (1-2,000 characters). Always asks the user "
        "for y/n approval before updating persona.md."
    )
    parallel_safe = False
    parameters: dict[str, Any] = {
        "type": "object",
        "properties": {
            "new_text": {
                "type": "string",
                "description": (
                    "Full replacement persona text (1-2,000 characters) shown to the "
                    "user for y/n approval."
                ),
            },
        },
        "required": ["new_text"],
        "additionalProperties": False,
    }

    def __init__(
        self,
        ask: AskFn,
        home: Path | None = None,
        *,
        cap: int = DEFAULT_PERSONA_CAP,
    ) -> None:
        self._ask = ask
        self._home = home
        self._cap = cap

    async def forward(self, new_text: str) -> str:  # type: ignore[override]
        if not isinstance(new_text, str) or "\x00" in new_text:
            msg = "Invalid persona: text must be a valid string."
            return ToolOutput(msg, summary=msg, ok=False)

        cleaned = new_text.strip()
        if not cleaned or len(cleaned) > self._cap:
            msg = (
                f"Invalid persona: must be between 1 and {self._cap:,} characters "
                f"(got {len(cleaned)})."
            )
            return ToolOutput(msg, summary=msg, ok=False)

        approved = await self._ask("Update persona?", cleaned)
        if not approved:
            return ToolOutput(
                "user declined",
                summary="user declined",
                ok=False,
                duration_ms=0,
            )

        exec_started = time.monotonic()
        await asyncio.to_thread(
            write_persona,
            self._home,
            cleaned,
            cap=self._cap,
        )
        elapsed_ms = max(0, int((time.monotonic() - exec_started) * 1000))
        return ToolOutput(
            "persona updated",
            summary="persona updated",
            ok=True,
            duration_ms=elapsed_ms,
        )


def build_persona_tool(
    ask: AskFn,
    home: Path | None = None,
    *,
    cap: int = DEFAULT_PERSONA_CAP,
) -> SetPersonaTool:
    """Construct the `SetPersonaTool` wired with the host's `ask` callback."""
    return SetPersonaTool(ask=ask, home=home, cap=cap)
