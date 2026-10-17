"""Tests for `aldwin.protocol` messages, unions, and wire rules."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from aldwin.host import InProcessTransport
from aldwin.protocol import (
    PROTOCOL_VERSION,
    ApprovalEvent,
    ApproveRequest,
    DoneEvent,
    ErrorEvent,
    Event,
    HelloRequest,
    HistoryEvent,
    HistoryItem,
    HistoryRequest,
    Request,
    SetModelRequest,
    StopRequest,
    TextEvent,
    TokenUsage,
    ToolCallEvent,
    ToolResultEvent,
    TurnRequest,
    parse_event,
    parse_message,
    parse_request,
)


@pytest.mark.parametrize(
    "request_msg",
    [
        HelloRequest(id="req_1", v_min=1, v_max=1),
        TurnRequest(id="req_2", text="How much disk space is free?"),
        StopRequest(id="req_3"),
        ApproveRequest(id="req_4", approval_id="ap_123", answer="yes"),
        HistoryRequest(id="req_5", limit=20),
        SetModelRequest(id="req_6", name="gpt-4o"),
    ],
)
def test_every_request_round_trips_json(request_msg: Request) -> None:
    """TC-01: Every Request message round-trips through JSON."""
    raw = request_msg.model_dump_json()
    parsed = parse_request(raw)
    assert parsed == request_msg
    assert parsed.v == PROTOCOL_VERSION
    assert parsed.channel == "cli"
    assert parsed.sender == "cli"
    assert parsed.id.startswith("req_")
    assert parse_message(raw) == request_msg


@pytest.mark.parametrize(
    "event_msg",
    [
        TextEvent(re="req_1", delta="Hello "),
        ToolCallEvent(
            re="req_1",
            call_id="call_1",
            name="run_command",
            args_preview='{"command": "df -h"}',
        ),
        ToolResultEvent(
            re="req_1",
            call_id="call_1",
            ok=True,
            summary="41 GB free",
            duration_ms=120,
        ),
        ApprovalEvent(
            re="req_1",
            approval_id="ap_123",
            summary="Run this command?",
            detail="df -h",
        ),
        HistoryEvent(
            re="req_5",
            messages=[
                HistoryItem(role="user", text="hi", at="2026-10-17T10:00:00Z"),
                HistoryItem(role="assistant", text="hello", at="2026-10-17T10:00:01Z"),
            ],
            interrupted=True,
        ),
        DoneEvent(
            re="req_1",
            reason="answered",
            usage=TokenUsage(input_tokens=42, output_tokens=18),
        ),
        ErrorEvent(
            re="req_1",
            code="auth_failed",
            message="Your OpenAI key was rejected. Run `aldwin setup`.",
        ),
    ],
)
def test_every_event_round_trips_json(event_msg: Event) -> None:
    """TC-01: Every Event message round-trips through JSON."""
    raw = event_msg.model_dump_json()
    parsed = parse_event(raw)
    assert parsed == event_msg
    assert parsed.v == PROTOCOL_VERSION
    assert parsed.type == "event"
    assert parsed.channel == "cli"
    assert parsed.sender == "host"
    assert parsed.re.startswith("req_")
    assert parse_message(raw) == event_msg


def test_unknown_fields_are_ignored_in_requests_and_events() -> None:
    """TC-02: Unknown fields are ignored in both directions."""
    req_json = json.dumps(
        {
            "v": 1,
            "type": "turn",
            "id": "req_extra",
            "text": "hello",
            "future_flag": True,
            "extra_nested": {"a": 1},
        }
    )
    req = parse_request(req_json)
    assert isinstance(req, TurnRequest)
    assert req.text == "hello"
    assert "future_flag" not in req.model_dump()

    ev_json = json.dumps(
        {
            "v": 1,
            "type": "event",
            "re": "req_extra",
            "kind": "done",
            "reason": "answered",
            "usage": {"input_tokens": 10, "output_tokens": 5, "cached_tokens": 3},
            "future_metric": 99,
        }
    )
    ev = parse_event(ev_json)
    assert isinstance(ev, DoneEvent)
    assert ev.usage == TokenUsage(input_tokens=10, output_tokens=5)
    assert "future_metric" not in ev.model_dump()


@pytest.mark.parametrize(
    "bad_request_payload",
    [
        {"v": 1, "type": "unknown_type", "id": "req_1"},
        {
            "v": 1,
            "type": "approve",
            "id": "req_1",
            "approval_id": "ap_1",
            "answer": "maybe",
        },
        {"v": 1, "type": "history", "id": "req_1", "limit": 0},
        {"v": 1, "type": "set_model", "id": "req_1", "name": ""},
    ],
)
def test_invalid_requests_are_rejected(bad_request_payload: dict[str, object]) -> None:
    """TC-16: Invalid request discriminators and field bounds raise ValidationError."""
    with pytest.raises(ValidationError):
        parse_request(json.dumps(bad_request_payload))


@pytest.mark.parametrize(
    "bad_event_payload",
    [
        {"v": 1, "type": "event", "re": "req_1", "kind": "unknown_kind"},
        {"v": 1, "type": "turn", "re": "req_1", "kind": "text", "delta": "hi"},
        {"v": 1, "type": "event", "kind": "text", "delta": "missing re"},
        {"v": 1, "type": "event", "re": "", "kind": "text", "delta": "empty re"},
        {
            "v": 1,
            "type": "event",
            "re": "req_1",
            "kind": "error",
            "code": "bad",
            "message": "x",
        },
    ],
)
def test_invalid_events_are_rejected(bad_event_payload: dict[str, object]) -> None:
    """TC-16: Invalid event discriminators, missing `re`, or wrong `type` fail."""
    with pytest.raises(ValidationError):
        parse_event(json.dumps(bad_event_payload))


def test_protocol_imports_only_stdlib_and_pydantic() -> None:
    """TC-15: `src/aldwin/protocol` imports only stdlib and `pydantic`."""
    protocol_dir = (
        Path(__file__).resolve().parent.parent / "src" / "aldwin" / "protocol"
    )
    allowed_roots = {"__future__", "typing", "uuid", "pydantic", "aldwin"}

    for py_file in protocol_dir.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    assert root in allowed_roots, (
                        f"Forbidden import {alias.name} in {py_file}"
                    )
                    assert not alias.name.startswith("aldwin.core")
                    assert not alias.name.startswith("agentling")
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
                assert root in allowed_roots, (
                    f"Forbidden import {node.module} in {py_file}"
                )
                assert not node.module.startswith("aldwin.core")
                assert not node.module.startswith("agentling")


async def test_in_process_transport_round_trips_and_closes_cleanly() -> None:
    """TC-19: `InProcessTransport` round-trips requests/events and closes cleanly."""
    new_conv_called = False

    async def on_new() -> None:
        nonlocal new_conv_called
        new_conv_called = True

    transport = InProcessTransport(on_new_conversation=on_new)
    client_handle = transport.connect()

    req1 = HelloRequest(id="req_h")
    req2 = TurnRequest(id="req_t", text="hello")
    await client_handle.send(req1)
    await client_handle.send(req2)

    assert await transport.receive_request() == req1
    assert await transport.receive_request() == req2

    ev1 = TextEvent(re="req_t", delta="world")
    ev2 = DoneEvent(re="req_t", reason="answered")
    await transport.send_event(ev1)
    await transport.send_event(ev2)
    await client_handle.new_conversation()
    assert new_conv_called is True

    await client_handle.close()

    collected: list[Event] = []
    async for ev in client_handle.events():
        collected.append(ev)

    assert collected == [ev1, ev2]
    assert await transport.receive_request() is None


async def test_in_process_transport_rejects_send_after_close() -> None:
    """TC-19: Sending a request after `close()` raises RuntimeError."""
    transport = InProcessTransport()
    client_handle = transport.connect()
    await transport.close()
    await transport.close()

    with pytest.raises(RuntimeError, match="closed"):
        await client_handle.send(HelloRequest(id="req_1"))

    await transport.send_event(DoneEvent(re="req_1", reason="ok"))
