"""Pydantic v2 message models for the Aldwin door-to-host protocol."""

from __future__ import annotations

from typing import Annotated, Any, Final, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, TypeAdapter

PROTOCOL_VERSION: Final[int] = 1

ApprovalAnswer = Literal["yes", "no"]
HistoryRole = Literal["user", "assistant", "tool"]
DoneReason = Literal["answered", "stopped", "max_steps", "ok"]
ErrorCode = Literal[
    "version_mismatch",
    "protocol",
    "busy",
    "unknown_approval",
    "unknown_model",
    "model_unavailable",
    "auth_failed",
    "rate_limited",
    "internal",
]


def _new_request_id() -> str:
    """Generate a unique protocol request identifier."""
    return f"req_{uuid4().hex[:12]}"


class TokenUsage(BaseModel):
    """Token consumption reported on a completed turn."""

    model_config = ConfigDict(extra="ignore")

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class HistoryItem(BaseModel):
    """One rendered message or tool summary returned in a history event."""

    model_config = ConfigDict(extra="ignore")

    role: HistoryRole
    text: str
    at: str | None = None


class RequestBase(BaseModel):
    """Envelope shared by all requests sent from a door to the host."""

    model_config = ConfigDict(extra="ignore")

    v: int = PROTOCOL_VERSION
    id: str = Field(default_factory=_new_request_id, min_length=1)
    channel: str = "cli"
    sender: str = "cli"


class HelloRequest(RequestBase):
    """Initial version handshake sent before any other request."""

    type: Literal["hello"] = "hello"
    v_min: int = Field(default=PROTOCOL_VERSION, ge=1)
    v_max: int = Field(default=PROTOCOL_VERSION, ge=1)


class TurnRequest(RequestBase):
    """Start a conversation turn with the given user text."""

    type: Literal["turn"] = "turn"
    text: str


class StopRequest(RequestBase):
    """Interrupt and cancel the currently running turn."""

    type: Literal["stop"] = "stop"


class ApproveRequest(RequestBase):
    """Answer a pending host approval prompt (`yes` or `no`)."""

    type: Literal["approve"] = "approve"
    approval_id: str = Field(min_length=1)
    answer: ApprovalAnswer


class HistoryRequest(RequestBase):
    """Fetch recent conversation messages and interrupted status."""

    type: Literal["history"] = "history"
    limit: int = Field(default=20, ge=1, le=500)


class SetModelRequest(RequestBase):
    """Switch the active model for subsequent turns."""

    type: Literal["set_model"] = "set_model"
    name: str = Field(min_length=1)


Request = Annotated[
    HelloRequest
    | TurnRequest
    | StopRequest
    | ApproveRequest
    | HistoryRequest
    | SetModelRequest,
    Field(discriminator="type"),
]


class EventBase(BaseModel):
    """Envelope shared by all events sent from the host to a door."""

    model_config = ConfigDict(extra="ignore")

    v: int = PROTOCOL_VERSION
    type: Literal["event"] = "event"
    re: str = Field(min_length=1)
    channel: str = "cli"
    sender: str = "host"


class TextEvent(EventBase):
    """A streamed chunk of assistant text."""

    kind: Literal["text"] = "text"
    delta: str


class ToolCallEvent(EventBase):
    """Notification that the agent is invoking a tool."""

    kind: Literal["tool_call"] = "tool_call"
    call_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    args_preview: str = ""


class ToolResultEvent(EventBase):
    """Outcome summary emitted after a tool call finishes."""

    kind: Literal["tool_result"] = "tool_result"
    call_id: str = Field(min_length=1)
    ok: bool
    summary: str = ""
    duration_ms: int = Field(default=0, ge=0)


class ApprovalEvent(EventBase):
    """Host request asking the door for user y/n approval before an action."""

    kind: Literal["approval"] = "approval"
    approval_id: str = Field(min_length=1)
    summary: str
    detail: str = ""


class HistoryEvent(EventBase):
    """Rendered past messages and interrupted status for session resume."""

    kind: Literal["history"] = "history"
    messages: list[HistoryItem] = Field(default_factory=list)
    interrupted: bool = False


class DoneEvent(EventBase):
    """Terminal success event ending a request."""

    kind: Literal["done"] = "done"
    reason: DoneReason
    usage: TokenUsage | None = None


class ErrorEvent(EventBase):
    """Terminal failure event ending a request."""

    kind: Literal["error"] = "error"
    code: ErrorCode
    message: str


Event = Annotated[
    TextEvent
    | ToolCallEvent
    | ToolResultEvent
    | ApprovalEvent
    | HistoryEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="kind"),
]


def _message_discriminator(value: Any) -> str:
    """Route a raw dict or model instance to the `event` or `request` branch."""
    msg_type = (
        value.get("type") if isinstance(value, dict) else getattr(value, "type", None)
    )
    if msg_type == "event":
        return "event"
    return "request"


Message = Annotated[
    Annotated[Request, Tag("request")] | Annotated[Event, Tag("event")],
    Discriminator(_message_discriminator),
]

REQUEST_ADAPTER: Final[TypeAdapter[Request]] = TypeAdapter(Request)
EVENT_ADAPTER: Final[TypeAdapter[Event]] = TypeAdapter(Event)
MESSAGE_ADAPTER: Final[TypeAdapter[Message]] = TypeAdapter(Message)


def parse_request(raw: str | bytes) -> Request:
    """Validate and parse a JSON request string or bytes into a `Request`."""
    return REQUEST_ADAPTER.validate_json(raw)


def parse_event(raw: str | bytes) -> Event:
    """Validate and parse a JSON event string or bytes into an `Event`."""
    return EVENT_ADAPTER.validate_json(raw)


def parse_message(raw: str | bytes) -> Message:
    """Validate and parse any protocol JSON frame into a `Request` or `Event`."""
    return MESSAGE_ADAPTER.validate_json(raw)
