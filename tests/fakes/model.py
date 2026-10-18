"""Deterministic fake implementation of Agentling's `Model` protocol for tests."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from agentling.models import (
    ChatMessage,
    Delta,
    Model,
    ToolCallDelta,
    ToolSpec,
    Usage,
    agglomerate_deltas,
)


@dataclass
class FakeTurn:
    """One scripted model turn replayed by `FakeModel`."""

    chunks: list[str] = field(default_factory=list)
    tool_calls: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)
    usage: Usage = field(
        default_factory=lambda: Usage(input_tokens=10, output_tokens=5)
    )
    exc: Exception | None = None
    hang_forever: bool = False
    hang_started: asyncio.Event = field(default_factory=asyncio.Event)
    hang_release: asyncio.Event = field(default_factory=asyncio.Event)

    @classmethod
    def text(
        cls,
        chunks: str | Sequence[str],
        *,
        deltas: int | None = None,
        usage: Usage | None = None,
    ) -> FakeTurn:
        """Stream `chunks` (or a single string split into `deltas`) as text deltas."""
        if isinstance(chunks, str):
            if deltas is not None:
                if deltas < 1 or len(chunks) < deltas:
                    raise ValueError(
                        f"Cannot split {len(chunks)}-char string into {deltas} deltas"
                    )
                if deltas > 1:
                    step = len(chunks) // deltas
                    parts = [
                        chunks[
                            i * step : (i + 1) * step if i < deltas - 1 else len(chunks)
                        ]
                        for i in range(deltas)
                    ]
                else:
                    parts = [chunks]
            else:
                parts = [chunks]
        else:
            parts = list(chunks)
        return cls(
            chunks=parts,
            usage=usage or Usage(input_tokens=10, output_tokens=len(parts)),
        )

    @classmethod
    def tool_call(
        cls,
        name: str,
        arguments: dict[str, Any],
        *,
        call_id: str = "call_1",
        content: str | None = None,
        usage: Usage | None = None,
    ) -> FakeTurn:
        """Emit one tool call (with optional assistant text)."""
        chunks = [content] if content else []
        return cls(
            chunks=chunks,
            tool_calls=[(call_id, name, arguments)],
            usage=usage or Usage(input_tokens=12, output_tokens=6),
        )

    @classmethod
    def final(
        cls,
        answer: str = "ok",
        *,
        usage: Usage | None = None,
    ) -> FakeTurn:
        """Emit a final assistant answer turn."""
        return cls.text(answer, usage=usage)

    @classmethod
    def final_answer_tool(
        cls,
        answer: str = "ok",
        *,
        call_id: str = "call_final",
        content: str | None = None,
        usage: Usage | None = None,
    ) -> FakeTurn:
        """Emit a turn that terminates via Agentling's `final_answer` tool."""
        return cls.tool_call(
            "final_answer",
            {"answer": answer},
            call_id=call_id,
            content=content,
            usage=usage,
        )

    @classmethod
    def error(cls, exc: Exception) -> FakeTurn:
        """Raise `exc` when the model is called."""
        return cls(exc=exc)

    @classmethod
    def raise_(cls, exc: Exception) -> FakeTurn:
        """Raise `exc` when the model is called (`FR-3` alias for `error`)."""
        return cls(exc=exc)

    @classmethod
    def hang(cls, *, prefix_chunks: Sequence[str] = ()) -> FakeTurn:
        """Stream `prefix_chunks`, set `hang_started`, and wait until cancelled."""
        return cls(chunks=list(prefix_chunks), hang_forever=True)


def text(
    chunks: str | Sequence[str],
    *,
    deltas: int | None = None,
    usage: Usage | None = None,
) -> FakeTurn:
    """Build a scripted text turn (`FR-3`)."""
    return FakeTurn.text(chunks, deltas=deltas, usage=usage)


def tool_call(
    name: str,
    arguments: dict[str, Any],
    *,
    call_id: str = "call_1",
    content: str | None = None,
    usage: Usage | None = None,
) -> FakeTurn:
    """Build a scripted tool-call turn (`FR-3`)."""
    return FakeTurn.tool_call(
        name,
        arguments,
        call_id=call_id,
        content=content,
        usage=usage,
    )


def final(
    answer: str = "ok",
    *,
    usage: Usage | None = None,
) -> FakeTurn:
    """Build a scripted final-answer turn (`FR-3`)."""
    return FakeTurn.final(answer, usage=usage)


def raise_(exc: Exception) -> FakeTurn:
    """Build a scripted exception turn (`FR-3`)."""
    return FakeTurn.raise_(exc)


def hang(*, prefix_chunks: Sequence[str] = ()) -> FakeTurn:
    """Build a scripted hanging turn that waits until cancelled (`FR-3`)."""
    return FakeTurn.hang(prefix_chunks=prefix_chunks)


class FakeModel(Model):
    """Scripted, network-free Agentling `Model` for unit and integration tests."""

    def __init__(self, turns: Sequence[FakeTurn] = ()) -> None:
        self._turns: list[FakeTurn] = list(turns)
        self.calls: list[list[ChatMessage]] = []
        self.tool_specs: list[list[ToolSpec] | None] = []

    @property
    def received_messages(self) -> list[ChatMessage]:
        """Return the `ChatMessage` list from the most recent model call."""
        return self.calls[-1] if self.calls else []

    def add_turns(self, *turns: FakeTurn) -> None:
        """Append scripted turns to the replay queue."""
        self._turns.extend(turns)

    def _next_turn(self) -> FakeTurn:
        if not self._turns:
            return FakeTurn.text(["ok"])
        return self._turns.pop(0)

    async def generate(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
    ) -> ChatMessage:
        """Collect `stream()` deltas into a single `ChatMessage`."""
        deltas: list[Delta] = []
        async for delta in self.stream(messages, tools=tools):
            deltas.append(delta)
        return agglomerate_deltas(deltas)

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolSpec] | None = None,
    ) -> AsyncIterator[Delta]:
        """Replay the next scripted `FakeTurn` as `Delta` chunks."""
        self.calls.append(list(messages))
        self.tool_specs.append(list(tools) if tools is not None else None)
        turn = self._next_turn()

        if turn.exc is not None:
            raise turn.exc

        for chunk in turn.chunks:
            yield Delta(content=chunk)
            await asyncio.sleep(0)

        if turn.hang_forever:
            turn.hang_started.set()
            await turn.hang_release.wait()

        for idx, (call_id, name, arguments) in enumerate(turn.tool_calls):
            yield Delta(
                tool_calls=[
                    ToolCallDelta(
                        index=idx,
                        id=call_id,
                        name=name,
                        arguments=json.dumps(arguments),
                    )
                ]
            )

        yield Delta(usage=turn.usage)
