"""Rich inline renderer for Aldwin's welcome, history, streaming markdown, and tools."""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections.abc import Callable
from datetime import datetime
from typing import Final, Literal

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.text import Text

from aldwin.cli.theme import (
    format_duration_ms,
    format_elapsed_s,
    format_relative_time,
    format_tokens,
)
from aldwin.protocol import (
    ApprovalAnswer,
    ApprovalEvent,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    HistoryEvent,
    ToolCallEvent,
    ToolResultEvent,
)

MAX_PANEL_WIDTH: Final[int] = 76

ERROR_COPY: Final[dict[ErrorCode, str]] = {
    "auth_failed": "Your OpenAI key was rejected. Run `aldwin setup`.",
    "rate_limited": (
        "OpenAI is rate-limiting requests right now. Wait a moment and try again."
    ),
    "model_unavailable": "Can't reach OpenAI. Check your connection and try again.",
    "unknown_model": (
        "That model isn't available on your account. Use /model to pick another."
    ),
    "busy": (
        "Aldwin is still working on your last request. Press Esc to stop it first."
    ),
    "version_mismatch": (
        "Protocol version mismatch with host. Restart Aldwin and try again."
    ),
    "protocol": "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log",
    "unknown_approval": (
        "That approval prompt has already expired. Try your request again."
    ),
    "internal": "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log",
}

INTERRUPTED_NOTICE: Final[str] = (
    "Your last request was interrupted. Say *continue* to pick it up."
)
MAX_STEPS_NOTICE: Final[str] = "Stopped after 15 steps. Say *continue* to keep going."
TIPS_LINE: Final[str] = 'Try: "what can you do?"  ·  /help  ·  Esc stops an answer'


_TRUNCATED_JSON_STR_RE: Final[re.Pattern[str]] = re.compile(
    r'^\{\s*"(?P<key>command|url|name|query|text|answer)"\s*:\s*"(?P<val>.*)$'
)


def _cap_text_width(text: str, limit: int) -> str:
    """Truncate `text` to at most `limit` characters with a trailing `…`."""
    if limit <= 1:
        return text[:limit]
    if len(text) <= limit:
        return text
    return f"{text[: limit - 1]}…"


def format_display_args(args_preview: str, tool_name: str = "") -> str:
    """Convert a JSON `args_preview` into a concise human-readable argument string."""
    cleaned = args_preview.strip()
    if not cleaned:
        return ""

    if cleaned.startswith("persona.md"):
        return cleaned

    if tool_name == "set_persona" or '"new_text"' in cleaned:
        try:
            parsed_persona = json.loads(cleaned)
            if isinstance(parsed_persona, dict) and isinstance(
                parsed_persona.get("new_text"), str
            ):
                raw_text = parsed_persona["new_text"].strip()
                count = len(raw_text.splitlines()) if raw_text else 0
                unit = "line" if count == 1 else "lines"
                return f"persona.md · {count} {unit}"
        except json.JSONDecodeError:
            count = max(1, cleaned.count("\\n") + 1)
            unit = "line" if count == 1 else "lines"
            return f"persona.md · {count} {unit}"

    if not (cleaned.startswith("{") and cleaned.endswith("}")):
        if cleaned.startswith("{"):
            match = _TRUNCATED_JSON_STR_RE.match(cleaned)
            if match is not None:
                key = match.group("key")
                val = match.group("val")
                if val.endswith('"'):
                    val = val[:-1]
                if key in ("query", "text"):
                    return f'"{val}"'
                return val
        return cleaned

    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        return cleaned

    if not isinstance(parsed, dict) or not parsed:
        return cleaned

    for raw_key in ("command", "url", "name"):
        if raw_key in parsed and isinstance(parsed[raw_key], str):
            return parsed[raw_key]

    for quoted_key in ("query", "text"):
        if quoted_key in parsed and isinstance(parsed[quoted_key], str):
            val = parsed[quoted_key]
            if len(parsed) == 1:
                return f'"{val}"'
            extras = [f"{k}={v}" for k, v in parsed.items() if k != quoted_key]
            return f'"{val}" ({", ".join(extras)})'

    if len(parsed) == 1:
        only_val = next(iter(parsed.values()))
        if isinstance(only_val, str):
            return f'"{only_val}"'
        return str(only_val)

    parts = [
        f'{k}="{v}"' if isinstance(v, str) else f"{k}={v}" for k, v in parsed.items()
    ]
    return ", ".join(parts)


def clean_tool_summary(tool_name: str, summary: str) -> str:
    """Normalise internal tool summaries (such as `# Using Aldwin` on `load_skill`)."""
    cleaned = summary.strip()
    if tool_name == "load_skill" and cleaned.startswith("#"):
        return "loaded"
    return cleaned


def _panel_width(console: Console) -> int:
    return min(console.width, MAX_PANEL_WIDTH)


def _opening_fence(stripped_line: str) -> str | None:
    """Return the fence marker (` ``` ` or `~~~`) if `stripped_line` opens a fence."""
    for char in ("`", "~"):
        if stripped_line.startswith(char * 3):
            run_len = len(stripped_line) - len(stripped_line.lstrip(char))
            return char * run_len
    return None


def _is_closing_fence(stripped_line: str, fence: str) -> bool:
    """Return `True` if `stripped_line` closes an open code `fence`."""
    fence_char = fence[0]
    if not stripped_line.startswith(fence):
        return False
    rest = stripped_line.lstrip(fence_char).strip()
    return rest == ""


def split_markdown_blocks(buffer: str) -> tuple[list[str], str]:
    """Split `buffer` on blank lines outside code fences into `(completed, remainder)`.

    Blank lines inside ` ``` ` or `~~~` fenced code blocks never split the block
    (`FR-6`, `TC-05`).
    """
    if not buffer:
        return [], ""

    completed: list[str] = []
    current_lines: list[str] = []
    open_fence: str | None = None

    for line in buffer.splitlines(keepends=True):
        stripped = line.strip()
        if open_fence is None:
            fence = _opening_fence(stripped)
            if fence is not None:
                open_fence = fence
                current_lines.append(line)
                continue

            if stripped == "" and line.endswith(("\n", "\r")):
                block = "".join(current_lines).strip()
                if block:
                    completed.append(block)
                current_lines = []
                continue

            current_lines.append(line)
        else:
            current_lines.append(line)
            if _is_closing_fence(stripped, open_fence):
                open_fence = None

    remainder = "".join(current_lines)
    return completed, remainder


def _tool_word(count: int) -> str:
    return "1 tool" if count == 1 else f"{count} tools"


def _approval_record_target(event: ApprovalEvent) -> str:
    detail = event.detail.strip()
    if not detail:
        return event.summary.strip()
    if "\n" in detail or "persona" in event.summary.lower():
        line_count = len(detail.splitlines())
        unit = "line" if line_count == 1 else "lines"
        return f"persona.md ({line_count} {unit})"
    return detail


class ChatRenderer:
    """Renders session headers, history, approvals, and errors to a Rich `Console`."""

    def __init__(self, console: Console, *, plain: bool = False) -> None:
        self.console = console
        self.plain = plain

    def render_welcome(
        self,
        *,
        first_run: bool,
        model: str,
        display_home: str,
    ) -> None:
        """Render the welcome panel or session header panel (`FR-2`, `FR-3`)."""
        width = _panel_width(self.console)
        if first_run:
            panel = Panel(
                Text("A personal assistant that runs on your computer.", style="fg"),
                title="[accent]Welcome to Aldwin[/accent]",
                title_align="left",
                border_style="border",
                width=width,
                padding=(0, 1),
            )
        else:
            max_home = max(12, width - len(model) - 18)
            shown_home = (
                display_home
                if len(display_home) <= max_home
                else f"…{display_home[-(max_home - 1) :]}"
            )
            panel = Panel(
                Text(TIPS_LINE, style="muted"),
                title=f"[accent]Aldwin[/accent] · {model} · {shown_home}",
                title_align="left",
                border_style="border",
                width=width,
                padding=(0, 1),
            )
        self.console.print(panel)

    def render_history(
        self,
        event: HistoryEvent,
        *,
        updated_at: str | None = None,
        now: datetime | None = None,
    ) -> None:
        """Render recent messages dimmed, divider, and interrupted notice (`FR-3`)."""
        if event.messages:
            last_at = updated_at
            for item in event.messages:
                if item.at:
                    last_at = item.at
                if item.role == "user":
                    self.console.print(Text(f"› {item.text}", style="muted"))
                else:
                    self.console.print(Text(f"  {item.text}", style="muted"))

            when = format_relative_time(last_at, now=now)
            if self.plain:
                self.console.print(f"──── continuing from {when} ────")
            else:
                self.console.print(
                    Rule(f"continuing from {when}", style="muted", characters="─")
                )

        if event.interrupted:
            if self.plain:
                self.console.print(INTERRUPTED_NOTICE)
            else:
                notice = Text("Your last request was interrupted. Say ", style="warn")
                notice.append("continue", style="italic")
                notice.append(" to pick it up.")
                self.console.print(notice)

    def render_new_conversation_divider(self) -> None:
        """Render the divider shown when `/new` starts a fresh conversation (`FR-5`)."""
        if self.plain:
            self.console.print("──── new conversation ────")
        else:
            self.console.print(Rule("new conversation", style="muted", characters="─"))

    def build_approval_panel(self, event: ApprovalEvent) -> Panel:
        """Construct the yellow bordered approval `Panel` (`FR-8`)."""
        detail_text = event.detail.strip() or event.summary.strip()
        is_multiline = "\n" in detail_text
        body = Text()
        detail_style = "" if self.plain else ("fg" if is_multiline else "code")
        body.append(detail_text, style=detail_style)
        separator = "\n\n[y/n]" if is_multiline else "  [y/n]"
        body.append(separator, style="warn")

        return Panel(
            body,
            title=event.summary or "Approve action?",
            title_align="left",
            border_style="warn",
            width=_panel_width(self.console),
            padding=(0, 1),
        )

    def ring_bell(self) -> None:
        """Emit the terminal bell character (`\\a`) when not in plain mode."""
        if not self.plain:
            try:
                self.console.file.write("\a")
                self.console.file.flush()
            except OSError:
                pass

    def render_approval_panel(self, event: ApprovalEvent) -> None:
        """Print the bordered approval panel and ring the terminal bell (`FR-8`)."""
        self.ring_bell()
        self.console.print(self.build_approval_panel(event))

    def render_error(self, code: ErrorCode, message: str = "") -> None:
        """Render a protocol `ErrorEvent` as one actionable line (`FR-10`, `TC-11`)."""
        copy = ERROR_COPY.get(code) or message or ERROR_COPY["internal"]
        line = Text()
        line.append("✗ ", style="err")
        line.append(copy, style="err")
        self.console.print(line)

    def render_status_notice(self, message: str) -> None:
        """Render a non-terminal status notice such as a retry message (`FR-10`)."""
        self.console.print(Text(f"  {message}", style="muted"))

    def start_turn(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> TurnRenderer:
        """Create and start a `TurnRenderer` for a new conversation turn."""
        turn = TurnRenderer(self, clock=clock)
        turn.start()
        return turn


class TurnRenderer:
    """Streams markdown blocks, tool cards, and a live status line for one turn."""

    def __init__(
        self,
        chat_renderer: ChatRenderer,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._chat = chat_renderer
        self.console = chat_renderer.console
        self.plain = chat_renderer.plain
        self._clock = clock

        self._started_at: float = self._clock()
        self._spinner_text: str = "Thinking…"
        self._tool_count: int = 0
        self._pending_tools: dict[str, tuple[str, str]] = {}
        self._printed_pending_ids: set[str] = set()
        self._markdown_buffer: str = ""
        self._has_printed_text: bool = False
        self._last_printed_kind: Literal["none", "tool", "text"] = "none"
        self._live: Live | None = None
        self._approval_live: Live | None = None
        self._ticker_task: asyncio.Task[None] | None = None

    @property
    def tool_count(self) -> int:
        """Return the number of completed tool calls in this turn."""
        return self._tool_count

    def elapsed_seconds(self) -> float:
        """Return elapsed seconds since the turn started."""
        return max(0.0, self._clock() - self._started_at)

    def format_status_line(self) -> str:
        """Format the live status text (`working · 12s · 3 tools`) (`FR-7`)."""
        elapsed = format_elapsed_s(self.elapsed_seconds())
        tools = _tool_word(self._tool_count)
        return f"working · {elapsed} · {tools}"

    def _build_pending_tool_line(self, name: str, display_args: str) -> Text:
        card = Text("  ⚙ ", style="muted", no_wrap=True, overflow="ellipsis")
        card.append(name, style="muted")
        if display_args:
            card.append(f"  {display_args}", style="muted")
        return card

    def _build_live_renderable(self) -> RenderableType:
        """Build the composite `Group` displayed inside the transient `Live` region."""
        parts: list[RenderableType] = []
        for call_id, (tool_name, display_args) in self._pending_tools.items():
            if call_id not in self._printed_pending_ids:
                parts.append(self._build_pending_tool_line(tool_name, display_args))

        if self._markdown_buffer.strip():
            parts.append(Markdown(self._markdown_buffer))

        status_str = f"{self._spinner_text}  ·  {self.format_status_line()}"
        parts.append(
            Spinner(
                "dots",
                text=Text(status_str, style="muted"),
                style="accent",
            )
        )
        return Group(*parts)

    def start(self) -> None:
        """Start the transient `Live` display and `0.5 s` refresh ticker (`FR-7`)."""
        self._started_at = self._clock()
        if self.plain:
            return
        if self._live is None:
            self._live = Live(
                get_renderable=self._build_live_renderable,
                console=self.console,
                refresh_per_second=12,
                transient=True,
                auto_refresh=False,
            )
            self._live.start(refresh=True)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and (self._ticker_task is None or self._ticker_task.done()):
            self._ticker_task = loop.create_task(self._run_ticker())

    async def _run_ticker(self) -> None:
        """Refresh the live status line every 0.5 s while the turn is active."""
        try:
            while True:
                await asyncio.sleep(0.5)
                self._refresh_live()
        except asyncio.CancelledError:
            raise

    def _stop_ticker(self) -> None:
        if self._ticker_task is not None and not self._ticker_task.done():
            self._ticker_task.cancel()
        self._ticker_task = None

    def _stop_approval_live(self) -> None:
        if self._approval_live is not None:
            self._approval_live.stop()
            self._approval_live = None

    def _stop_live(self) -> None:
        self._stop_ticker()
        self._stop_approval_live()
        if self._live is not None:
            self._live.stop()
            self._live = None

    def close(self) -> None:
        """Ensure the `Live` region and `0.5 s` ticker task are stopped."""
        self._stop_live()

    def _refresh_live(self) -> None:
        if self._live is not None and self._live.is_started:
            self._live.refresh()

    def _print_markdown_block(self, block: str) -> None:
        cleaned = block.strip()
        if not cleaned:
            return
        if self._last_printed_kind in ("tool", "text"):
            self.console.print()
        if self.plain:
            self.console.print(cleaned)
        else:
            self.console.print(Markdown(cleaned))
        self._has_printed_text = True
        self._last_printed_kind = "text"

    def _flush_markdown_buffer(self, *, suffix: str = "") -> None:
        raw = self._markdown_buffer.strip()
        self._markdown_buffer = ""
        if not raw:
            if suffix:
                self.console.print(Text(suffix, style="muted"))
            return
        combined = f"{raw} {suffix}".strip() if suffix else raw
        self._print_markdown_block(combined)

    def on_text(self, delta: str) -> None:
        """Append a `text` delta and flush finished markdown blocks (`FR-6`)."""
        if not delta:
            return
        self._markdown_buffer += delta
        completed, remainder = split_markdown_blocks(self._markdown_buffer)
        self._markdown_buffer = remainder
        for block in completed:
            self._print_markdown_block(block)
        self._refresh_live()

    def on_tool_call(self, event: ToolCallEvent) -> None:
        """Flush buffered text, track pending `⚙` card, and update spinner (`FR-7`)."""
        self._flush_markdown_buffer()
        display_args = format_display_args(event.args_preview, tool_name=event.name)
        self._pending_tools[event.call_id] = (event.name, display_args)
        self._spinner_text = f"Running {event.name}…"

        if self.plain or not self.console.is_interactive:
            self._printed_pending_ids.add(event.call_id)
            self.console.print(
                self._build_pending_tool_line(event.name, display_args),
                overflow="ellipsis",
                no_wrap=True,
            )
            self._last_printed_kind = "tool"

        self._refresh_live()

    def on_tool_result(self, event: ToolResultEvent) -> None:
        """Print a single-line `✓` or `✗` tool card with args, duration, and summary."""
        tool_name, display_args = self._pending_tools.pop(event.call_id, ("tool", ""))
        was_printed_pending = event.call_id in self._printed_pending_ids
        self._printed_pending_ids.discard(event.call_id)

        self._tool_count += 1
        if self._pending_tools:
            next_tool, _ = next(iter(self._pending_tools.values()))
            self._spinner_text = f"Running {next_tool}…"
        else:
            self._spinner_text = "Thinking…"

        duration = format_duration_ms(event.duration_ms)
        summary = clean_tool_summary(tool_name, event.summary)
        include_args = bool(display_args) and not was_printed_pending

        if event.ok:
            detail = f"{duration} · {summary}" if summary else duration
            card = Text("  ✓ ", style="ok", no_wrap=True, overflow="ellipsis")
            card.append(tool_name, style="muted")
            if include_args:
                avail = max(
                    12,
                    self.console.width - len("  ✓ ") - len(tool_name) - len(detail) - 4,
                )
                capped_args = _cap_text_width(display_args, avail)
                card.append(f"  {capped_args}", style="muted")
            card.append(f"  {detail}", style="muted")
        else:
            reason = summary or "failed"
            card = Text("  ✗ ", style="err", no_wrap=True, overflow="ellipsis")
            card.append(tool_name, style="err")
            if include_args:
                avail = max(
                    12,
                    self.console.width - len("  ✗ ") - len(tool_name) - len(reason) - 4,
                )
                capped_args = _cap_text_width(display_args, avail)
                card.append(f"  {capped_args}", style="muted")
            card.append(f"  {reason}", style="err")

        self.console.print(card, overflow="ellipsis", no_wrap=True)
        self._last_printed_kind = "tool"
        self._refresh_live()

    def on_status(self, message: str) -> None:
        """Render a non-terminal status event (`FR-10`)."""
        self._chat.render_status_notice(message)
        self._refresh_live()

    def pause_for_approval(self, event: ApprovalEvent) -> None:
        """Stop turn `Live` and show the transient approval box (`FR-8`)."""
        self._flush_markdown_buffer()
        self._stop_live()
        if not self.plain and self.console.is_interactive:
            self._chat.ring_bell()
            panel = self._chat.build_approval_panel(event)
            self._approval_live = Live(
                panel,
                console=self.console,
                transient=True,
                auto_refresh=False,
            )
            self._approval_live.start(refresh=True)
        else:
            self._chat.render_approval_panel(event)

    def resolve_approval(self, event: ApprovalEvent, answer: ApprovalAnswer) -> None:
        """Collapse the approval box into a one-line `✓ approved` / `✗ declined` row."""
        self._stop_approval_live()
        target = _approval_record_target(event)
        if answer == "yes":
            line = Text("  ✓ approved", style="ok", no_wrap=True, overflow="ellipsis")
        else:
            line = Text("  ✗ declined", style="warn", no_wrap=True, overflow="ellipsis")
        if target:
            line.append(f"  {target}", style="muted")
        self.console.print(line, overflow="ellipsis", no_wrap=True)
        self._last_printed_kind = "tool"
        self.resume_after_approval()

    def resume_after_approval(self) -> None:
        """Restart `Live` after the user answers the approval prompt (`FR-8`)."""
        self._stop_approval_live()
        if self.plain:
            return
        if self._live is None:
            self._live = Live(
                get_renderable=self._build_live_renderable,
                console=self.console,
                refresh_per_second=12,
                transient=True,
                auto_refresh=False,
            )
            self._live.start(refresh=True)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None and (self._ticker_task is None or self._ticker_task.done()):
            self._ticker_task = loop.create_task(self._run_ticker())

    def finish_done(self, event: DoneEvent) -> int:
        """Stop `Live`, flush final markdown, print end line, and return tokens."""
        self._stop_live()
        elapsed = format_elapsed_s(self.elapsed_seconds())
        tools = _tool_word(self._tool_count)
        total_tokens = (
            event.usage.input_tokens + event.usage.output_tokens
            if event.usage is not None
            else 0
        )

        if event.reason == "stopped":
            self._flush_markdown_buffer(suffix="*(stopped)*")
            end_line = Text("  ✓ ", style="ok")
            end_line.append(f"done · {elapsed} · {tools} (stopped)", style="muted")
            self.console.print(end_line)
            return total_tokens

        self._flush_markdown_buffer()

        if event.reason == "max_steps":
            if self.plain:
                self.console.print(MAX_STEPS_NOTICE)
            else:
                notice = Text("Stopped after 15 steps. Say ", style="warn")
                notice.append("continue", style="italic")
                notice.append(" to keep going.")
                self.console.print(notice)
            return total_tokens

        end_line = Text("  ✓ ", style="ok")
        summary = f"done · {elapsed} · {tools}"
        if event.usage is not None:
            summary += f" · {format_tokens(total_tokens)} tokens"
        end_line.append(summary, style="muted")
        self.console.print(end_line)
        return total_tokens

    def finish_error(self, event: ErrorEvent) -> None:
        """Stop `Live`, flush partial output, and print the error line (`FR-10`)."""
        self._flush_markdown_buffer()
        self._stop_live()
        self._chat.render_error(event.code, event.message)
