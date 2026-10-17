"""Context manager that rebuilds the system prompt and trims conversation history."""

from __future__ import annotations

import logging
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Final

from agentling import ChatMessage

from aldwin.core.home import read_base_instructions
from aldwin.core.settings import LimitsConfig

logger = logging.getLogger("aldwin.core.context")

PERSONA_FILENAME: Final[str] = "persona.md"

_WARNED_PERSONA_CAPS: set[tuple[str, int, int, int]] = set()


def _read_default_persona() -> str:
    """Read the package-shipped default `persona.md` asset."""
    return (
        files("aldwin.core.assets")
        .joinpath(PERSONA_FILENAME)
        .read_text(encoding="utf-8")
        .strip()
    )


def read_persona_stub(home: Path, cap: int) -> str:
    """Read `persona.md` from `home` (falling back to shipped default) capped at `cap`.

    Truncates in the prompt only (never on disk) and logs a warning once per
    file modification when the file exceeds `cap` characters.
    """
    persona_path = home.expanduser().resolve() / PERSONA_FILENAME
    mtime_ns = 0
    try:
        stat = persona_path.stat()
        mtime_ns = stat.st_mtime_ns
        raw = persona_path.read_text(encoding="utf-8").strip()
    except OSError:
        raw = ""

    if not raw:
        raw = _read_default_persona()

    if len(raw) > cap:
        warn_key = (str(persona_path), mtime_ns, len(raw), cap)
        if warn_key not in _WARNED_PERSONA_CAPS:
            if len(_WARNED_PERSONA_CAPS) >= 64:
                _WARNED_PERSONA_CAPS.clear()
            _WARNED_PERSONA_CAPS.add(warn_key)
            logger.warning(
                "Persona file %s is %d characters (cap %d); truncating in prompt.",
                persona_path,
                len(raw),
                cap,
            )
        return raw[:cap]

    return raw


def memory_block_stub(home: Path, limits: LimitsConfig) -> str:
    """Return the `<memory>` block for the system prompt (stubbed until ENG-381)."""
    del home, limits
    return ""


def _find_matching_assistant_index(
    conversation: list[ChatMessage],
    tool_index: int,
) -> int | None:
    """Find the assistant message index that initiated `conversation[tool_index]`."""
    tool_call_id = conversation[tool_index].tool_call_id
    fallback_idx: int | None = None

    for idx in range(tool_index - 1, -1, -1):
        msg = conversation[idx]
        if msg.role != "assistant" or not msg.tool_calls:
            continue
        if tool_call_id is not None and any(
            tc.id == tool_call_id for tc in msg.tool_calls
        ):
            return idx
        if fallback_idx is None:
            fallback_idx = idx

    return fallback_idx


def _trim_conversation(
    conversation: list[ChatMessage],
    max_exchanges: int,
) -> list[ChatMessage]:
    """Keep the last `max_exchanges` user turns without splitting tool calls/results."""
    if not conversation:
        return []

    user_indices = [idx for idx, msg in enumerate(conversation) if msg.role == "user"]
    if len(user_indices) > max_exchanges:
        start_idx = user_indices[-max_exchanges]
    else:
        start_idx = 0

    while start_idx > 0:
        extended_idx = start_idx
        for idx in range(start_idx, len(conversation)):
            if conversation[idx].role == "tool":
                assistant_idx = _find_matching_assistant_index(conversation, idx)
                if assistant_idx is not None and assistant_idx < extended_idx:
                    extended_idx = assistant_idx
        if extended_idx == start_idx:
            break
        start_idx = extended_idx

    return list(conversation[start_idx:])


def build_context(
    home: Path,
    limits: LimitsConfig,
    *,
    persona_reader: Callable[[Path, int], str] | None = None,
    memory_builder: Callable[[Path, LimitsConfig], str] | None = None,
) -> Callable[[list[ChatMessage]], list[ChatMessage]]:
    """Build the Agentling `context_manager` hook for `home` and `limits` (`FR-4`).

    On every model call:
    1. Replaces `messages[0]` with Agentling's instructions (which already
       include the skill catalog) + `## Persona` + the `<memory>` block.
    2. Keeps only the last `limits.context_exchanges` user turns and everything
       after the earliest kept user turn.
    3. Extends the kept window backward if needed so an assistant tool call is
       never separated from its `tool` result messages.
    """
    resolved_home = home.expanduser().resolve()
    read_persona_fn = persona_reader or read_persona_stub
    build_memory_fn = memory_builder or memory_block_stub

    def _context_manager(messages: list[ChatMessage]) -> list[ChatMessage]:
        if messages and messages[0].role == "system":
            base_instructions = messages[0].content
            conversation = messages[1:]
        else:
            base_instructions = read_base_instructions()
            conversation = messages

        persona_text = read_persona_fn(resolved_home, limits.persona_chars)
        memory_text = build_memory_fn(resolved_home, limits).strip()

        sections: list[str] = []
        if base_instructions.strip():
            sections.append(base_instructions.rstrip())
        if persona_text:
            sections.append(f"## Persona\n{persona_text}")
        if memory_text:
            sections.append(memory_text)

        system_message = ChatMessage(
            role="system",
            content="\n\n".join(sections),
        )
        kept_conversation = _trim_conversation(
            conversation,
            limits.context_exchanges,
        )
        return [system_message, *kept_conversation]

    return _context_manager
