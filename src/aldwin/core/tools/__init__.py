"""Built-in Agentling tools, tool summary contract, and registry for Aldwin."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from agentling import Tool

from aldwin.core.settings import SecretsConfig

if TYPE_CHECKING:
    from aldwin.core.tools.web_search import SearchProvider

AskFn = Callable[[str, str], Awaitable[bool]]

MAX_SUMMARY_CHARS: Final[int] = 120

BUILTIN_TOOL_NAMES: Final[tuple[str, ...]] = (
    "web_search",
    "web_fetch",
    "run_command",
    "remember",
    "recall",
    "set_persona",
)


class ToolOutput(str):
    """String return value for tools that also carries card metadata (`summary`, `ok`).

    Because `ToolOutput` subclasses `str` and returns `self` from `__str__()`,
    Agentling stores the full string in session memory while preserving
    `.summary` and `.ok` on `ToolResult.content` for `AldwinHost`.
    """

    summary: str
    ok: bool

    def __new__(
        cls,
        content: str = "",
        *,
        summary: str = "",
        ok: bool = True,
    ) -> ToolOutput:
        instance = super().__new__(cls, content)
        instance.summary = summary
        instance.ok = ok
        return instance

    def __str__(self) -> str:
        return self

    def __deepcopy__(self, _memo: object) -> ToolOutput:
        return ToolOutput(
            super().__str__(),
            summary=getattr(self, "summary", ""),
            ok=getattr(self, "ok", True),
        )

    def __add__(self, other: str) -> ToolOutput:
        return ToolOutput(
            super().__add__(other),
            summary=self.summary,
            ok=self.ok,
        )


from aldwin.core.tools.memory_tools import (  # noqa: E402
    RecallTool,
    RememberTool,
    build_memory_tools,
)
from aldwin.core.tools.persona_tool import (  # noqa: E402
    SetPersonaTool,
    build_persona_tool,
)


@dataclass(frozen=True, slots=True)
class ToolInfo:
    """One-line descriptor for `/tools` in the CLI (`FR-7`)."""

    name: str
    description: str
    requires_approval: bool


_BUILTIN_TOOL_CATALOG: Final[tuple[ToolInfo, ...]] = (
    ToolInfo(
        name="web_search",
        description="Search the web with Tavily and return top numbered results.",
        requires_approval=False,
    ),
    ToolInfo(
        name="web_fetch",
        description="Fetch a web page as readable text (up to 20,000 characters).",
        requires_approval=False,
    ),
    ToolInfo(
        name="run_command",
        description="Run a shell command in the launch folder after y/n approval.",
        requires_approval=True,
    ),
    ToolInfo(
        name="remember",
        description="Save a short, durable fact to today's memory notes.",
        requires_approval=False,
    ),
    ToolInfo(
        name="recall",
        description="Search recent memory notes by keywords.",
        requires_approval=False,
    ),
    ToolInfo(
        name="set_persona",
        description="Replace Aldwin's persona.md after y/n approval.",
        requires_approval=True,
    ),
)


def _shorten_summary(text: str, limit: int = MAX_SUMMARY_CHARS) -> str:
    """Collapse whitespace to a single line and cap at `limit` characters."""
    collapsed = " ".join(text.split())
    if len(collapsed) <= limit:
        return collapsed
    if limit <= 1:
        return collapsed[:limit]
    return f"{collapsed[: limit - 1]}…"


def summarize_tool_result(
    name: str,
    content: str,
    *,
    is_error: bool = False,
) -> tuple[bool, str]:
    """Return `(ok, one_line_summary)` for a completed tool call (`FR-5`)."""
    del name
    if isinstance(content, ToolOutput):
        ok = False if is_error else content.ok
        return ok, _shorten_summary(content.summary)

    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return (not is_error), _shorten_summary(stripped)
    return (not is_error), ""


def builtin_tools(
    ask: AskFn,
    home: Path | None = None,
    secrets: SecretsConfig | None = None,
    *,
    search_provider: SearchProvider | None = None,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    persona_cap: int | None = None,
    session_id_getter: Callable[[], str] | None = None,
) -> list[Tool]:
    """Return the fixed 6 built-in tools for Aldwin 0.0.1 (`FR-6`)."""
    from aldwin.core.persona import DEFAULT_PERSONA_CAP
    from aldwin.core.tools.run_command import build_run_command_tool
    from aldwin.core.tools.web_fetch import build_web_fetch_tool
    from aldwin.core.tools.web_search import build_web_search_tool

    web_search_tool = build_web_search_tool(
        home=home,
        secrets=secrets,
        provider=search_provider,
    )
    web_fetch_tool = build_web_fetch_tool()
    run_command_tool = build_run_command_tool(
        ask=ask,
        cwd=cwd,
        env=env,
    )
    remember_tool, recall_tool = build_memory_tools(
        home=home,
        session_id_getter=session_id_getter,
    )
    persona_tool = build_persona_tool(
        ask=ask,
        home=home,
        cap=persona_cap if persona_cap is not None else DEFAULT_PERSONA_CAP,
    )
    return [
        web_search_tool,
        web_fetch_tool,
        run_command_tool,
        remember_tool,
        recall_tool,
        persona_tool,
    ]


def list_builtin_tools() -> list[ToolInfo]:
    """Return one-line descriptions of the built-in tools for `/tools` (`FR-7`)."""
    return list(_BUILTIN_TOOL_CATALOG)


__all__ = [
    "AskFn",
    "BUILTIN_TOOL_NAMES",
    "RecallTool",
    "RememberTool",
    "SetPersonaTool",
    "ToolInfo",
    "ToolOutput",
    "build_memory_tools",
    "build_persona_tool",
    "builtin_tools",
    "list_builtin_tools",
    "summarize_tool_result",
]
