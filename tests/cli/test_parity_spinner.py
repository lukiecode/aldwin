"""Spinner / status-line parity tests."""

from __future__ import annotations

from io import StringIO

from rich.console import Console

from aldwin.cli.render import ChatRenderer, TurnRenderer
from aldwin.cli.theme import GLYPH_FRAME_S, SPINNER_FRAMES, SPINNER_GLYPHS
from aldwin.protocol import DoneEvent, ToolCallEvent


def test_spinner_frames_over_two_seconds() -> None:
    """All glyphs appear across 2 s; clock-driven, not a fresh Spinner."""
    assert len(SPINNER_GLYPHS) >= 6
    assert len(set(SPINNER_FRAMES)) == len(SPINNER_GLYPHS)
    clock = {"t": 0.0}
    console = Console(file=StringIO(), force_terminal=True, width=80)
    turn = TurnRenderer(ChatRenderer(console, plain=False), clock=lambda: clock["t"])
    seen: set[str] = set()
    while clock["t"] < 2.0:
        seen.add(turn.spinner_glyph(now=clock["t"]))
        clock["t"] += GLYPH_FRAME_S
    assert seen == set(SPINNER_GLYPHS)


def test_status_verbs_and_trailer() -> None:
    """Thinking → Writing → Running <label>; trailer after 3 s."""
    clock = {"t": 0.0}
    console = Console(file=StringIO(), force_terminal=True, width=80)
    turn = TurnRenderer(ChatRenderer(console, plain=False), clock=lambda: clock["t"])
    assert "Thinking" in turn._spinner_text
    turn.on_text("hi")
    assert "Writing" in turn._spinner_text
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="t1", name="web_search", summary="search")
    )
    assert "Running" in turn._spinner_text
    clock["t"] = 4.0
    trailer = turn.format_status_line()
    assert "esc to interrupt" in trailer
    turn.finish_done(DoneEvent(re="r1", reason="answered"))
