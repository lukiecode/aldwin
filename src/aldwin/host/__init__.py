"""Aldwin pass (`host`): receives door requests, runs turns, and manages sessions."""

from aldwin.host.in_process import (
    BootstrapResult,
    ClientBootstrapError,
    ClientTransport,
    InProcessTransport,
    NewConversationHandler,
    PlatformNotSupportedError,
    bootstrap_host,
)
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

__all__ = [
    "AgentBuilder",
    "AldwinHost",
    "ApprovalBroker",
    "AskFn",
    "BootstrapResult",
    "ClientBootstrapError",
    "ClientTransport",
    "InProcessTransport",
    "NewConversationHandler",
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
    "shorten_one_line",
]
