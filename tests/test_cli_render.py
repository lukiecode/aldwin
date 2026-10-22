"""Unit tests for Aldwin CLI theme, markdown block splitting, and Rich rendering."""

from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Group
from rich.text import Text

from aldwin.cli.render import (
    ChatRenderer,
    clean_tool_summary,
    format_display_args,
    split_markdown_blocks,
)
from aldwin.cli.theme import (
    create_console,
    filter_canonical_models,
    format_duration_ms,
    format_elapsed_s,
    format_relative_time,
    format_tokens,
)
from aldwin.protocol import (
    ApprovalEvent,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    HistoryEvent,
    HistoryItem,
    TokenUsage,
    ToolCallEvent,
    ToolResultEvent,
)


def test_theme_formatters() -> None:
    """Formatters produce expected compact strings for tokens, durations, and IDs."""
    assert format_tokens(0) == "0"
    assert format_tokens(450) == "450"
    assert format_tokens(1100) == "1.1k"
    assert format_tokens(6200) == "6.2k"

    assert format_duration_ms(200) == "0.2s"
    assert format_duration_ms(1200) == "1.2s"
    assert format_elapsed_s(24.8) == "24s"

    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    assert format_relative_time("2026-10-18T09:15:00+00:00", now=now) == "today 09:15"
    assert (
        format_relative_time("2026-10-17T18:02:00+00:00", now=now) == "yesterday 18:02"
    )
    assert (
        format_relative_time("2026-10-10T14:30:00+00:00", now=now) == "2026-10-10 14:30"
    )
    assert format_relative_time(None, now=now) == "earlier"
    assert format_relative_time("not-a-timestamp", now=now) == "earlier"


def test_split_markdown_blocks_preserves_code_fences_with_blank_lines() -> None:
    """Blank lines inside ``` or ~~~ fences never split the block."""
    raw = (
        "First paragraph.\n\n"
        "```python\n"
        "def add(a: int, b: int) -> int:\n\n"
        "    return a + b\n"
        "```\n\n"
        "Trailing unfinished"
    )
    completed, remainder = split_markdown_blocks(raw)
    assert len(completed) == 2
    assert completed[0] == "First paragraph."
    assert "def add(a: int, b: int) -> int:\n\n    return a + b" in completed[1]
    assert remainder == "Trailing unfinished"


def test_split_markdown_blocks_holds_unclosed_fence_in_remainder() -> None:
    """An unclosed code fence stays in remainder even when it contains blank lines."""
    raw = "Intro.\n\n~~~bash\necho one\n\necho two\n"
    completed, remainder = split_markdown_blocks(raw)
    assert completed == ["Intro."]
    assert remainder.startswith("~~~bash")
    assert "echo two" in remainder


def test_render_history_last_20_dimmed_with_divider() -> None:
    """20 history messages render dimmed in order followed by divider."""
    buf = io.StringIO()
    console = create_console(
        plain=False,
        width=80,
        record=True,
        file=buf,
        force_terminal=True,
    )
    renderer = ChatRenderer(console, plain=False)

    items = [
        HistoryItem(
            role="user" if i % 2 == 0 else "assistant",
            text=f"message-{i + 6}",
        )
        for i in range(20)
    ]
    event = HistoryEvent(re="req_hist", messages=items, interrupted=False)
    now = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
    renderer.render_history(
        event,
        updated_at="2026-10-17T18:02:00+00:00",
        now=now,
    )

    text = console.export_text()
    assert "› message-6" in text
    assert "message-25" in text
    assert text.index("message-6") < text.index("message-25")
    assert "continuing from yesterday 18:02" in text
    assert "interrupted" not in text


def test_render_history_interrupted_notice_shown_once() -> None:
    """Interrupted flag shows the interrupted recovery notice once."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    event = HistoryEvent(
        re="req_hist",
        messages=[HistoryItem(role="user", text="check disk")],
        interrupted=True,
    )
    renderer.render_history(event)

    text = console.export_text()
    assert "Your last request was interrupted." in text
    assert "continue" in text
    assert text.count("Your last request was interrupted.") == 1


def test_turn_renderer_streams_three_markdown_blocks_with_code_fence() -> None:
    """3 markdown blocks incl. code fence print permanently in order once."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    now = 100.0
    turn = renderer.start_turn(clock=lambda: now)
    turn.on_text("First block of text.\n\n")
    turn.on_text("```python\ndef greet():\n\n    return 'hi'\n```\n\n")
    turn.on_text("Third final block.")
    now = 104.0
    tokens = turn.finish_done(
        DoneEvent(
            re="r1",
            reason="answered",
            usage=TokenUsage(input_tokens=800, output_tokens=300),
        )
    )

    assert tokens == 1100
    text = console.export_text()
    assert text.count("First block of text.") == 1
    assert text.count("def greet():") == 1
    assert text.count("Third final block.") == 1
    assert (
        text.index("First block of text.")
        < text.index("def greet():")
        < text.index("Third final block.")
    )
    assert "✓ done · 4s · 0 tools · 1.1k tokens" in text


def test_turn_renderer_tool_call_and_result_cards() -> None:
    """tool_call and tool_result render ⚙, ✓, and ✗ cards with duration."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    now = 10.0
    turn = renderer.start_turn(clock=lambda: now)
    turn.on_tool_call(
        ToolCallEvent(
            re="r1",
            call_id="c1",
            name="web_search",
            args_preview='"Python 3.14"',
        )
    )
    assert turn._spinner_text == "Running web_search…"

    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=True,
            summary="5 results",
            duration_ms=1200,
        )
    )
    turn.on_tool_call(
        ToolCallEvent(
            re="r1",
            call_id="c2",
            name="run_command",
            args_preview="sudo rm -rf /",
        )
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c2",
            ok=False,
            summary="sudo is not allowed",
            duration_ms=5,
        )
    )
    now = 15.0
    turn.finish_done(DoneEvent(re="r1", reason="answered"))

    text = console.export_text()
    assert '⚙ web_search  "Python 3.14"' in text
    assert "✓ web_search  1.2s · 5 results" in text
    assert "✗ run_command  sudo is not allowed" in text
    assert "✓ done · 5s · 2 tools" in text


def test_render_error_codes_map_to_house_copy() -> None:
    """Every protocol ErrorCode renders its exact one-line copy and next step."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    codes: list[ErrorCode] = [
        "auth_failed",
        "rate_limited",
        "model_unavailable",
        "unknown_model",
        "busy",
        "internal",
    ]
    for code in codes:
        renderer.render_error(code, "raw message")

    text = console.export_text()
    assert "Your OpenAI key was rejected. Run `aldwin setup`." in text
    assert "OpenAI is rate-limiting requests right now." in text
    assert "Can't reach OpenAI. Check your connection and try again." in text
    assert "Use /model to pick another." in text
    assert "Press Esc to stop it first." in text
    assert "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log" in text
    assert "Traceback" not in text


def test_plain_mode_has_no_ansi_spinner_live_or_bell() -> None:
    """Plain mode emits zero ANSI escape codes, spinner frames, or bell chars."""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    renderer.render_welcome(first_run=False, model="gpt-4o", display_home="~/.aldwin")
    renderer.render_history(
        HistoryEvent(
            re="h1",
            messages=[HistoryItem(role="user", text="hello")],
            interrupted=True,
        )
    )
    turn = renderer.start_turn()
    turn.on_text("Paragraph one.\n\nParagraph two.")
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="run_command", args_preview="df -h")
    )
    turn.pause_for_approval(
        ApprovalEvent(
            re="r1",
            approval_id="ap_1",
            summary="Run this command?",
            detail="df -h",
        )
    )
    turn.resume_after_approval()
    turn.on_tool_result(
        ToolResultEvent(
            re="r1", call_id="c1", ok=True, summary="exit 0", duration_ms=200
        )
    )
    turn.finish_done(DoneEvent(re="r1", reason="answered"))

    raw = buf.getvalue()
    assert "\x1b[" not in raw
    assert "\a" not in raw
    for frame in "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏":
        assert frame not in raw
    assert "Paragraph one." in raw
    assert "Paragraph two." in raw
    assert "Run this command?" in raw
    assert "✓ run_command  0.2s · exit 0" in raw


def test_turn_renderer_status_ticker_retry_notice_and_max_steps() -> None:
    """Status line formats elapsed & tools; retry notice and max_steps render."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    now = 0.0
    turn = renderer.start_turn(clock=lambda: now)
    for idx in range(3):
        cid = f"c{idx}"
        turn.on_tool_call(ToolCallEvent(re="r1", call_id=cid, name="web_search"))
        turn.on_tool_result(
            ToolResultEvent(
                re="r1", call_id=cid, ok=True, summary="ok", duration_ms=100
            )
        )
    now = 12.4
    assert turn.format_status_line() == "working · 12s · 3 tools"

    turn.on_status("OpenAI is busy, retrying (2 of 3)…")
    turn.finish_done(DoneEvent(re="r1", reason="max_steps"))

    text = console.export_text()
    assert "OpenAI is busy, retrying (2 of 3)…" in text
    assert "Stopped after 15 steps. Say continue to keep going." in text


def test_turn_renderer_flushes_narration_before_tool_card() -> None:
    """Buffered text before a tool call prints above the ⚙ tool card."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    turn = renderer.start_turn()
    turn.on_text("Let me check your disk usage.")
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="run_command", args_preview="df -h")
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1", call_id="c1", ok=True, summary="exit 0", duration_ms=100
        )
    )
    turn.on_text("You have 41 GB free.")
    turn.finish_done(DoneEvent(re="r1", reason="answered"))

    text = console.export_text()
    idx_narration = text.index("Let me check your disk usage.")
    idx_tool = text.index("⚙ run_command")
    idx_answer = text.index("You have 41 GB free.")
    assert idx_narration < idx_tool < idx_answer


def test_narrow_60_column_rendering() -> None:
    """Panels and tool cards wrap cleanly within 60 columns."""
    buf = io.StringIO()
    console = create_console(width=60, record=True, file=buf)
    renderer = ChatRenderer(console)

    renderer.render_welcome(first_run=False, model="gpt-4o", display_home="~/.aldwin")
    renderer.render_approval_panel(
        ApprovalEvent(
            re="r1",
            approval_id="ap_1",
            summary="Run this command?",
            detail="df -h /System/Volumes/Data/Users/fola/Documents",
        )
    )
    turn = renderer.start_turn()
    turn.on_tool_call(
        ToolCallEvent(
            re="r1",
            call_id="c1",
            name="web_fetch",
            args_preview="https://docs.python.org/3/whatsnew/3.14.html",
        )
    )
    turn.finish_error(ErrorEvent(re="r1", code="model_unavailable", message="offline"))

    text = console.export_text()
    for line in text.splitlines():
        assert len(line) <= 60, f"Line exceeded 60 columns ({len(line)}): {line!r}"


def test_multiline_approval_panel_and_turn_close_cleanup() -> None:
    """Multi-line approval detail places [N] no on its own line; close stops Live."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    renderer.render_approval_panel(
        ApprovalEvent(
            re="r1",
            approval_id="ap_persona",
            summary="Update persona?",
            detail="Line one of new persona.\nLine two of new persona.",
        )
    )
    text = console.export_text()
    assert "Line one of new persona." in text
    assert "Line two of new persona." in text
    assert "[N] no" in text

    turn = renderer.start_turn()
    assert turn._live is not None
    turn.close()
    assert turn._live is None


def test_format_display_args_and_clean_tool_summary() -> None:
    """JSON tool args unwrap to clean strings; load_skill header becomes 'loaded'."""
    assert (
        format_display_args('{"query": "Folarin Akinloye Cuvama"}')
        == '"Folarin Akinloye Cuvama"'
    )
    assert format_display_args('{"command": "df -h"}') == "df -h"
    assert (
        format_display_args('{"url": "https://agentling.dev"}')
        == "https://agentling.dev"
    )
    assert format_display_args('{"name": "using-aldwin"}') == "using-aldwin"
    assert (
        format_display_args('{"text": "The user\'s name is Folarin."}')
        == '"The user\'s name is Folarin."'
    )
    assert (
        format_display_args(
            '{"new_text": "Line 1\\nLine 2\\nLine 3"}', tool_name="set_persona"
        )
        == "persona.md · 3 lines"
    )
    assert clean_tool_summary("load_skill", "# Using Aldwin") == "loaded"
    assert clean_tool_summary("web_search", "5 results") == "5 results"


def test_wide_120_column_panels_cap_at_76_columns() -> None:
    """Panels cap width at <= 76 columns on wide 120-column terminals."""
    buf = io.StringIO()
    console = create_console(width=120, record=True, file=buf)
    renderer = ChatRenderer(console)
    renderer.render_welcome(first_run=True, model="gpt-4o", display_home="~/.aldwin")

    for line in console.export_text().splitlines():
        assert len(line.rstrip()) <= 76


def test_filter_canonical_models() -> None:
    """filter_canonical_models hides dated snapshots and puts current model first."""
    raw = [
        "gpt-5-2025-08-07",
        "gpt-5",
        "gpt-5-chat-latest",
        "gpt-5.2-codex",
        "gpt-4.1",
        "gpt-4.1-2025-04-14",
        "gpt-4o",
    ]
    filtered, hidden = filter_canonical_models(raw, "gpt-4o")
    assert filtered == ["gpt-4o", "gpt-5", "gpt-4.1"]
    assert hidden == 4


def test_resolve_approval_collapses_to_one_line_and_list_paragraph_spacing() -> None:
    """resolve_approval prints one-line record; markdown blocks have blank line."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)
    turn = renderer.start_turn()

    ev = ApprovalEvent(
        re="r1",
        approval_id="ap_1",
        summary="Run this command?",
        detail="df -h",
    )
    turn.pause_for_approval(ev)
    turn.resolve_approval(ev, "yes")
    turn.on_text("- item one\n- item two\n\nThis overview condenses the result.")
    turn.finish_done(DoneEvent(re="r1", reason="answered"))

    text = console.export_text()
    assert "✓ approved  df -h" in text
    assert "\n\nThis overview condenses the result." in text


def test_format_display_args_unwraps_truncated_json_fallback() -> None:
    """`format_display_args` unwraps truncated JSON strings without raw `{`."""
    assert (
        format_display_args(
            '{"command": "echo alpha_beta_gamma_delta…',
            tool_name="run_command",
        )
        == "echo alpha_beta_gamma_delta…"
    )
    assert (
        format_display_args(
            '{"query": "long search query truncated…',
            tool_name="web_search",
        )
        == '"long search query truncated…"'
    )


def test_tool_result_80_cols_preserves_duration_and_summary() -> None:
    """80-col tool result row caps `display_args` and keeps `duration`."""
    buf = io.StringIO()
    console = create_console(
        width=80,
        record=True,
        file=buf,
        force_terminal=True,
    )
    renderer = ChatRenderer(console)
    turn = renderer.start_turn()
    try:
        turn.on_tool_call(
            ToolCallEvent(
                re="r1",
                call_id="c1",
                name="run_command",
                args_preview="echo " + ("x" * 110),
            )
        )
        turn.on_tool_result(
            ToolResultEvent(
                re="r1",
                call_id="c1",
                ok=True,
                summary="exit 0",
                duration_ms=120,
            )
        )
        turn.on_tool_call(
            ToolCallEvent(
                re="r1",
                call_id="c2",
                name="run_command",
                args_preview="echo " + ("y" * 110),
            )
        )
        turn.on_tool_result(
            ToolResultEvent(
                re="r1",
                call_id="c2",
                ok=False,
                summary="exit 1",
                duration_ms=50,
            )
        )
    finally:
        turn.close()

    exported = console.export_text().splitlines()
    ok_lines = [
        line[line.index("  ✓ run_command") :].rstrip()
        for line in exported
        if "  ✓ run_command" in line
    ]
    assert len(ok_lines) == 1
    assert len(ok_lines[0]) <= 80
    assert ok_lines[0].startswith("  ✓ run_command  echo xxx")
    assert ok_lines[0].endswith("0.1s · exit 0")

    err_lines = [
        line[line.index("  ✗ run_command") :].rstrip()
        for line in exported
        if "  ✗ run_command" in line
    ]
    assert len(err_lines) == 1
    assert len(err_lines[0]) <= 80
    assert err_lines[0].startswith("  ✗ run_command  echo yyy")
    assert err_lines[0].endswith("exit 1")


def test_approval_panel_strips_ansi_bidi_and_redacts_secrets() -> None:
    """Panel `summary` and `detail` strip ANSI/bidi and redact secrets."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    ev = ApprovalEvent(
        re="r1",
        approval_id="ap_san",
        tool="run_command",
        tier="ask",
        summary="Edit \x1b[31mnotes\u202e.md",
        detail="OPENAI_API_KEY=sk-1234567890abcdefghijklmno",
        risky=["pipes into a shell", "network download"],
    )
    renderer.render_approval_panel(ev)
    text = console.export_text()
    assert "Edit notes.md" in text
    assert "\u202e" not in text
    assert "sk-1234567890" not in text
    assert "OPENAI_API_KEY=‹redacted›" in text
    assert "⚠ pipes into a shell · network download" in text


def test_diff_approval_panel_folds_at_24_lines_and_expands() -> None:
    """Diff approval panel folds after 24 lines and expands when requested."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    renderer = ChatRenderer(console)

    diff_lines = ["--- a/notes.md", "+++ b/notes.md", "@@ -1,28 +1,28 @@"] + [
        f"+added line {i}" if i % 2 == 0 else f"-removed line {i}" for i in range(1, 28)
    ]
    assert len(diff_lines) == 30
    ev = ApprovalEvent(
        re="r1",
        approval_id="ap_diff",
        tool="edit_file",
        tier="diff",
        summary="Edit notes.md",
        detail="\n".join(diff_lines),
        grantable=True,
        grant_scope="file",
    )

    renderer.render_approval_panel(ev, expanded=False)
    folded_text = console.export_text()
    assert "… 6 more lines (ctrl+o to expand)" in folded_text
    assert "+added line 26" not in folded_text
    assert "[y] yes  [a] yes for this file this session  [N] no" in folded_text

    buf_exp = io.StringIO()
    console_exp = create_console(width=80, record=True, file=buf_exp)
    renderer_exp = ChatRenderer(console_exp)
    renderer_exp.render_approval_panel(ev, expanded=True)
    expanded_text = console_exp.export_text()
    assert "+added line 26" in expanded_text
    assert "more lines (ctrl+o to expand)" not in expanded_text


def test_diff_approval_clips_long_lines_until_expanded() -> None:
    """Folded Diff panels clip long lines; expand rebuilds without the notice."""
    marker = "UNIQUE_LONG_MARKER_ABCDEF"
    long_plus = "+" + ("x" * 200) + marker
    ev = ApprovalEvent(
        re="r1",
        approval_id="ap_long",
        tool="edit_file",
        tier="diff",
        summary="Edit a.py",
        detail=f"--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n{long_plus}",
        grantable=True,
        grant_scope="file",
    )

    folded_buf = io.StringIO()
    folded_console = create_console(
        width=60, record=True, file=folded_buf, force_terminal=True
    )
    ChatRenderer(folded_console).render_approval_panel(ev, expanded=False)
    folded = folded_console.export_text()
    assert "long lines truncated (ctrl+o to expand)" in folded
    assert marker not in folded

    # Inspect the expanded panel renderable directly — export_text can
    # ellipsize tall panels in some CI terminals.
    exp_console = create_console(width=60, force_terminal=True)
    panel = ChatRenderer(exp_console).build_approval_panel(ev, expanded=True)
    assert isinstance(panel.renderable, Group)
    joined = "\n".join(
        part.plain if isinstance(part, Text) else str(part)
        for part in panel.renderable.renderables
    )
    assert "long lines truncated" not in joined
    assert marker in joined.replace("\n", "")


def test_resolve_diff_approval_uses_summary_not_persona() -> None:
    """Declined Diff rows echo the edit summary, not a persona.md line count."""
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    turn = ChatRenderer(console).start_turn()
    ev = ApprovalEvent(
        re="r1",
        approval_id="ap_d",
        tool="edit_file",
        tier="diff",
        summary="Edit notes.md (+1 −1)",
        detail="--- a/notes.md\n+++ b/notes.md\n@@ -1 +1 @@\n-old\n+new",
    )
    turn.pause_for_approval(ev)
    turn.resolve_approval(ev, "no")
    text = console.export_text()
    assert "✗ declined  Edit notes.md (+1 −1)" in text
    assert "persona.md" not in text


def test_tc44_verbose_help_readme(aldwin_home: Path) -> None:
    """`/verbose` shows preview lines, and help and the README list it."""
    config = aldwin_home / "config.yaml"
    before = config.read_bytes() if config.is_file() else b""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    assert renderer.verbose is False
    preview = "\n".join(f"{number}\trow" for number in range(1, 16))
    turn = renderer.start_turn()
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="read_file", summary="Read a.txt")
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=True,
            summary="Read a.txt · lines 1–15 of 15",
            preview=preview,
        )
    )
    quiet = console.export_text()
    assert "Read a.txt · lines 1–15 of 15" in quiet
    assert "1\trow" not in quiet

    renderer.set_verbose(True)
    turn_on = renderer.start_turn()
    turn_on.on_tool_call(
        ToolCallEvent(re="r1", call_id="c2", name="read_file", summary="Read a.txt")
    )
    turn_on.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c2",
            ok=True,
            summary="Read a.txt · lines 1–15 of 15",
            preview=preview,
        )
    )
    shown = console.export_text()
    preview_rows = [
        line
        for line in shown.splitlines()
        if line.startswith("    ") and line.strip().endswith("row")
    ]
    assert [line.strip().split()[0] for line in preview_rows] == [
        str(number) for number in range(1, 13)
    ]
    assert "13" not in {line.strip().split()[0] for line in preview_rows}
    assert "verbose: on" in shown
    renderer.set_verbose(False)
    assert "verbose: off" in console.export_text()
    after = config.read_bytes() if config.is_file() else b""
    assert after == before

    help_buf = io.StringIO()
    help_console = create_console(plain=True, width=80, record=True, file=help_buf)
    help_renderer = ChatRenderer(help_console, plain=True)

    async def _run_help() -> None:
        from fakes.host import FakeClient

        from aldwin.cli.commands import handle_slash_command

        await handle_slash_command(
            "/help",
            client=FakeClient(),
            renderer=help_renderer,
            prompt_line=_noop_prompt,
        )

    asyncio.run(_run_help())
    help_text = help_console.export_text()
    assert "/verbose" in help_text
    assert "/workspace" in help_text
    readme = Path("README.md").read_text(encoding="utf-8")
    slash = readme.split("### Slash commands", 1)[1].split("### Keyboard", 1)[0]
    assert "/verbose" in slash
    assert "/workspace" in slash


async def _noop_prompt(_label: str) -> str:
    return ""


def test_tc45_pending_glyph_and_plain() -> None:
    """File tools use a bullet while they run. Other tools keep the gear."""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    turn = renderer.start_turn()
    turn.on_tool_call(
        ToolCallEvent(re="r1", call_id="c1", name="read_file", summary="Read a.txt")
    )
    turn.on_tool_call(ToolCallEvent(re="r1", call_id="c2", name="web_search"))
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=False,
            summary="Read .env · blocked: may contain secrets",
        )
    )
    text = console.export_text()
    assert "● Read a.txt" in text
    assert "⚙ web_search" in text
    assert "Read .env · blocked: may contain secrets" in text

    color_buf = io.StringIO()
    color = create_console(
        width=80,
        record=True,
        file=color_buf,
        force_terminal=True,
    )
    color_turn = ChatRenderer(color).start_turn()
    color_turn.on_tool_call(ToolCallEvent(re="r1", call_id="c1", name="read_file"))
    color_turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=False,
            summary="Read .env · blocked: may contain secrets",
        )
    )
    failure = [segment for segment in color._record_buffer if "✗" in segment.text]
    assert failure
    style = failure[0].style
    assert style is not None
    assert "err" in str(style) or style.color is not None


def test_tool_rows_strip_terminal_controls() -> None:
    """File-tool rows and previews drop ANSI and other terminal controls."""
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)
    renderer.set_verbose(True)
    console.export_text()
    turn = renderer.start_turn()
    turn.on_tool_call(
        ToolCallEvent(
            re="r1",
            call_id="c1",
            name="read_file",
            summary="Read \x1b[2Ja.txt",
        )
    )
    turn.on_tool_result(
        ToolResultEvent(
            re="r1",
            call_id="c1",
            ok=True,
            summary="Read \x1b[2Ja.txt · lines 1–1 of 1",
            preview="1\t\x1b[31mrow",
        )
    )
    shown = console.export_text()
    assert "\x1b" not in shown
    assert "Read a.txt" in shown
    assert "row" in shown
