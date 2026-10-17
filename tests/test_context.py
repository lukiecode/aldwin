"""Tests for `aldwin.core.context` (`build_context`, persona cap, exchange trimming)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import pytest
from agentling import ChatMessage, ToolCall

from aldwin.core.context import build_context
from aldwin.core.home import ensure_home
from aldwin.core.settings import LimitsConfig


def test_build_context_trims_to_last_20_user_exchanges(aldwin_home: Path) -> None:
    """TC-02: 30 user turns are trimmed to the last 20 with system message first."""
    ensure_home(aldwin_home)
    limits = LimitsConfig(context_exchanges=20)
    ctx = build_context(aldwin_home, limits)

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content="Base instructions"),
    ]
    for i in range(1, 31):
        messages.append(ChatMessage(role="user", content=f"user-{i}"))
        messages.append(ChatMessage(role="assistant", content=f"assistant-{i}"))

    original_snapshot = list(messages)
    result = ctx(messages)

    assert messages == original_snapshot
    assert result[0].role == "system"
    user_messages = [m for m in result if m.role == "user"]
    assert len(user_messages) == 20
    assert user_messages[0].content == "user-11"
    assert user_messages[-1].content == "user-30"
    assert result[1].content == "user-11"
    assert result[-1].content == "assistant-30"


def test_build_context_never_splits_tool_call_from_results(aldwin_home: Path) -> None:
    """TC-03: Kept window extends backward when cut lands mid tool-call sequence."""
    ensure_home(aldwin_home)
    limits = LimitsConfig(context_exchanges=20)
    ctx = build_context(aldwin_home, limits)

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content="Base instructions"),
    ]
    for i in range(1, 11):
        messages.append(ChatMessage(role="user", content=f"old-user-{i}"))
        messages.append(ChatMessage(role="assistant", content=f"old-assistant-{i}"))

    # Assistant tool call sits immediately before the 20th-from-last user message,
    # while its tool result arrives after that user message.
    tool_call_msg = ChatMessage(
        role="assistant",
        content="",
        tool_calls=[
            ToolCall(id="call_boundary", name="web_search", arguments={"q": "aldwin"})
        ],
    )
    tool_result_msg = ChatMessage(
        role="tool",
        content="1. Aldwin docs",
        tool_call_id="call_boundary",
    )
    messages.append(tool_call_msg)
    messages.append(ChatMessage(role="user", content="kept-user-1"))
    messages.append(tool_result_msg)
    messages.append(ChatMessage(role="assistant", content="kept-assistant-1"))

    for i in range(2, 21):
        messages.append(ChatMessage(role="user", content=f"kept-user-{i}"))
        messages.append(ChatMessage(role="assistant", content=f"kept-assistant-{i}"))

    result = ctx(messages)

    assert result[0].role == "system"
    assert result[1] == tool_call_msg
    assert result[2].content == "kept-user-1"
    assert result[3] == tool_result_msg

    # Every tool message in the output is preceded by its matching assistant call.
    seen_tool_call_ids: set[str] = set()
    for msg in result[1:]:
        for tc in msg.tool_calls:
            seen_tool_call_ids.add(tc.id)
        if msg.role == "tool":
            assert msg.tool_call_id in seen_tool_call_ids


def test_build_context_reloads_persona_between_turns(aldwin_home: Path) -> None:
    """TC-04: Editing `persona.md` between turns updates the next system message."""
    ensure_home(aldwin_home)
    persona_path = aldwin_home / "persona.md"
    persona_path.write_text("Be warm and friendly.\n", encoding="utf-8")

    ctx = build_context(aldwin_home, LimitsConfig())
    messages = [
        ChatMessage(role="system", content="Base instructions"),
        ChatMessage(role="user", content="Hello"),
    ]

    first = ctx(messages)
    assert "## Persona\nBe warm and friendly." in first[0].content

    persona_path.write_text("Be terse and direct.\n", encoding="utf-8")
    second = ctx(messages)
    assert "## Persona\nBe terse and direct." in second[0].content
    assert "Be warm and friendly." not in second[0].content


def test_build_context_truncates_persona_over_cap_and_warns_once(
    aldwin_home: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TC-05: Persona > 2,000 chars is truncated in the prompt and logged once."""
    ensure_home(aldwin_home)
    persona_path = aldwin_home / "persona.md"
    overlong_persona = "A" * 2500
    persona_path.write_text(overlong_persona, encoding="utf-8")

    ctx = build_context(aldwin_home, LimitsConfig(persona_chars=2000))
    messages = [
        ChatMessage(role="system", content="Base instructions"),
        ChatMessage(role="user", content="Hi"),
    ]

    with caplog.at_level(logging.WARNING, logger="aldwin.core.context"):
        out1 = ctx(messages)
        out2 = ctx(messages)

    expected_persona_section = "## Persona\n" + ("A" * 2000)
    assert out1[0].content.endswith(expected_persona_section)
    assert out2[0].content.endswith(expected_persona_section)
    assert "A" * 2001 not in out1[0].content

    # File on disk is untouched.
    assert len(persona_path.read_text(encoding="utf-8")) == 2500

    # Warning logged exactly once for the unchanged file.
    warnings = [
        rec
        for rec in caplog.records
        if rec.levelname == "WARNING" and "persona.md" in rec.getMessage()
    ]
    assert len(warnings) == 1
    assert "2000" in warnings[0].getMessage()


def test_build_context_system_order_with_memory_block_and_missing_persona(
    aldwin_home: Path,
) -> None:
    """TC-15: System message order (instructions -> persona -> memory) and fallback."""
    ensure_home(aldwin_home)
    (aldwin_home / "persona.md").unlink()

    memory_block = "<memory>\n## Profile\nLives in London.\n</memory>"
    ctx = build_context(
        aldwin_home,
        LimitsConfig(),
        memory_builder=lambda _home, _limits: memory_block,
    )
    messages = [
        ChatMessage(role="system", content="BASE+SKILLS"),
        ChatMessage(role="user", content="Hello"),
    ]

    result = ctx(messages)
    sys_text = result[0].content

    assert sys_text.startswith("BASE+SKILLS\n\n## Persona\n")
    assert sys_text.endswith(f"\n\n{memory_block}")
    persona_idx = sys_text.index("## Persona\n")
    memory_idx = sys_text.index("<memory>")
    assert 0 < persona_idx < memory_idx


def test_build_context_performance_under_5ms_for_200_messages(
    aldwin_home: Path,
) -> None:
    """TC-20: `build_context` completes in < 5 ms for a 200-message history."""
    ensure_home(aldwin_home)
    ctx = build_context(aldwin_home, LimitsConfig())

    messages: list[ChatMessage] = [
        ChatMessage(role="system", content="Base instructions and skill catalog"),
    ]
    for i in range(100):
        messages.append(ChatMessage(role="user", content=f"Question {i}"))
        if i % 3 == 0:
            messages.append(
                ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=[
                        ToolCall(id=f"c_{i}", name="web_search", arguments={"q": "x"})
                    ],
                )
            )
            messages.append(
                ChatMessage(
                    role="tool",
                    content=f"Result {i}",
                    tool_call_id=f"c_{i}",
                )
            )
        messages.append(ChatMessage(role="assistant", content=f"Answer {i}"))

    assert len(messages) >= 200

    # Warm up filesystem read once, then measure median of 5 calls.
    ctx(messages)
    durations_ms: list[float] = []
    for _ in range(5):
        t0 = time.perf_counter()
        out = ctx(messages)
        durations_ms.append((time.perf_counter() - t0) * 1000)
        assert len([m for m in out if m.role == "user"]) == 20

    assert sorted(durations_ms)[len(durations_ms) // 2] < 5.0
