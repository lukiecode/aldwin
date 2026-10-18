"""Aldwin pass (`host`): receives door requests, runs turns, and manages sessions."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aldwin.host.in_process import (
    BootstrapResult,
    ClientBootstrapError,
    ClientTransport,
    InProcessTransport,
    MemorySummary,
    NewConversationHandler,
    NoteEntry,
    PersonaSummary,
    PlatformNotSupportedError,
    bootstrap_host,
    get_memory_summary,
    get_persona_summary,
    get_persona_text,
    pop_profile_truncation_warning,
)

if TYPE_CHECKING:
    from aldwin.host.runner import (
        AgentBuilder,
        AldwinHost,
        ApprovalBroker,
        AskFn,
        RequestScope,
        classify_turn_error,
        format_args_preview,
        format_tool_summary,
    )
    from aldwin.host.sessions import (
        SessionMeta,
        SessionStore,
        StoredSession,
        derive_session_title,
        shorten_one_line,
    )

_RUNNER_EXPORTS = {
    "AgentBuilder",
    "AldwinHost",
    "ApprovalBroker",
    "AskFn",
    "RequestScope",
    "classify_turn_error",
    "format_args_preview",
    "format_tool_summary",
}

_SESSIONS_EXPORTS = {
    "SessionMeta",
    "SessionStore",
    "StoredSession",
    "derive_session_title",
    "shorten_one_line",
}


def __getattr__(name: str) -> Any:
    """Lazily import `runner` and `sessions` on attribute access."""
    if name in _RUNNER_EXPORTS:
        from aldwin.host import runner

        return getattr(runner, name)
    if name in _SESSIONS_EXPORTS:
        from aldwin.host import sessions

        return getattr(sessions, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentBuilder",
    "AldwinHost",
    "ApprovalBroker",
    "AskFn",
    "BootstrapResult",
    "ClientBootstrapError",
    "ClientTransport",
    "InProcessTransport",
    "MemorySummary",
    "NewConversationHandler",
    "NoteEntry",
    "PersonaSummary",
    "PlatformNotSupportedError",
    "RequestScope",
    "SessionMeta",
    "SessionStore",
    "StoredSession",
    "bootstrap_host",
    "classify_turn_error",
    "derive_session_title",
    "format_args_preview",
    "format_tool_summary",
    "get_memory_summary",
    "get_persona_summary",
    "get_persona_text",
    "pop_profile_truncation_warning",
    "shorten_one_line",
]
