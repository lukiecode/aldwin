"""Rich theme tokens, console factory, prompt_toolkit styles, and display formatters."""

from __future__ import annotations

import re
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import IO, Final

from prompt_toolkit.styles import Style
from rich.console import Console
from rich.theme import Theme

THEME: Final[Theme] = Theme(
    {
        "fg": "default",
        "accent": "cyan",
        "code": "cyan",
        "muted": "bright_black",
        "border": "bright_black",
        "ok": "green",
        "warn": "yellow",
        "err": "red",
        "diff.plus": "green",
        "diff.minus": "red",
        "diff.meta": "cyan",
        "diff.context": "default",
        "approval.keys": "bright_black",
        "approval.default": "bold",
        "markdown.paragraph": "default",
        "markdown.text": "default",
        "markdown.em": "italic",
        "markdown.emph": "italic",
        "markdown.strong": "bold",
        "markdown.code": "cyan",
        "markdown.code_block": "cyan",
        "markdown.block_quote": "bright_black",
        "markdown.list": "default",
        "markdown.item": "default",
        "markdown.item.bullet": "bright_black",
        "markdown.item.number": "bright_black",
        "markdown.hr": "bright_black",
        "markdown.h1": "bold underline",
        "markdown.h2": "bold",
        "markdown.h3": "bold",
        "markdown.h4": "bold bright_black",
        "markdown.h5": "italic",
        "markdown.h6": "bright_black",
        "markdown.link": "cyan",
        "markdown.link_url": "cyan underline",
    }
)

PROMPT_STYLE: Final[Style] = Style.from_dict(
    {
        "": "ansibrightblack",
        "prompt": "ansicyan bold",
        "bottom-toolbar": "noreverse ansibrightblack",
        "bottom-toolbar.text": "noreverse ansibrightblack",
        "completion-menu": "bg:ansiblack ansiwhite",
        "completion-menu.completion": "bg:ansiblack ansiwhite",
        "completion-menu.completion.current": "bg:ansicyan ansiblack bold",
        "completion-menu.meta.completion": "bg:ansiblack ansibrightblack",
        "completion-menu.meta.completion.current": "bg:ansicyan ansiblack",
    }
)

PLAIN_PROMPT_STYLE: Final[Style] = Style.from_dict(
    {
        "": "",
        "prompt": "",
        "bottom-toolbar": "noreverse",
        "bottom-toolbar.text": "noreverse",
    }
)

_DATED_SNAPSHOT_RE: Final[re.Pattern[str]] = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_SNAPSHOT_SUFFIXES: Final[tuple[str, ...]] = (
    "-chat-latest",
    "-codex",
    "-codex-max",
    "-codex-mini",
)


def model_context_window(model: str) -> int:
    """Return the nominal context window in tokens for `model`."""
    lowered = model.strip().lower()
    if lowered.startswith(("gpt-4.1", "gpt-5")):
        return 400_000
    if lowered.startswith(("o3", "o4")):
        return 200_000
    return 128_000


def context_left_percent(model: str, used_tokens: int) -> int:
    """Return remaining context percentage (`0..100`) for `model`."""
    window = max(1, model_context_window(model))
    clamped_used = max(0, int(used_tokens))
    remaining = max(0.0, float(window - clamped_used)) / float(window) * 100.0
    return max(0, min(100, round(remaining)))


def is_canonical_model(model_id: str) -> bool:
    """Return `True` if `model_id` is a canonical model alias rather than a snapshot."""
    cleaned = model_id.strip().lower()
    if not cleaned:
        return False
    if _DATED_SNAPSHOT_RE.search(cleaned):
        return False
    return not any(cleaned.endswith(suffix) for suffix in _SNAPSHOT_SUFFIXES)


def filter_canonical_models(
    models: Sequence[str],
    current_model: str,
) -> tuple[list[str], int]:
    """Filter `models` to canonical aliases with `current_model` first.

    Returns `(filtered_models, hidden_snapshot_count)`. Falls back to the full
    list if filtering would leave zero models.
    """
    unique: list[str] = []
    seen: set[str] = set()
    for name in models:
        cleaned = name.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            unique.append(cleaned)

    current_clean = current_model.strip()
    canonical = [m for m in unique if m == current_clean or is_canonical_model(m)]
    if not canonical:
        canonical = list(unique)

    if current_clean and current_clean in canonical:
        canonical = [
            current_clean,
            *[m for m in canonical if m != current_clean],
        ]
    elif current_clean:
        canonical = [current_clean, *canonical]

    hidden_count = max(0, len(unique) - len(canonical))
    return canonical, hidden_count


def create_console(
    *,
    plain: bool = False,
    width: int | None = None,
    record: bool = False,
    file: IO[str] | None = None,
    force_terminal: bool | None = None,
) -> Console:
    """Create the shared Rich `Console` configured with Aldwin's theme."""
    return Console(
        theme=THEME,
        no_color=plain,
        highlight=False,
        width=width,
        record=record,
        file=file,
        force_terminal=False if plain and force_terminal is None else force_terminal,
    )


def format_tokens(count: int) -> str:
    """Format a token count concisely (`6200` -> `'6.2k'`, `450` -> `'450'`)."""
    clamped = max(0, int(count))
    if clamped < 1000:
        return str(clamped)
    return f"{clamped / 1000:.1f}k"


def format_duration_ms(duration_ms: int) -> str:
    """Format milliseconds as seconds with one decimal place (`1200` -> `'1.2s'`)."""
    seconds = max(0, int(duration_ms)) / 1000.0
    return f"{seconds:.1f}s"


def format_elapsed_s(seconds: float) -> str:
    """Format elapsed turn duration in whole seconds (`24.3` -> `'24s'`)."""
    whole = max(0, int(seconds))
    return f"{whole}s"


def format_relative_time(
    iso_ts: str | None,
    *,
    now: datetime | None = None,
) -> str:
    """Format an ISO-8601 timestamp for the history divider."""
    if not iso_ts:
        return "earlier"
    try:
        parsed = datetime.fromisoformat(iso_ts)
    except ValueError:
        return "earlier"

    if now is None:
        local_now = datetime.now().astimezone()
    elif now.tzinfo is None:
        local_now = now.astimezone()
    else:
        local_now = now
    local_ts = parsed.astimezone(local_now.tzinfo)
    time_part = local_ts.strftime("%H:%M")

    if local_ts.date() == local_now.date():
        return f"today {time_part}"
    if local_ts.date() == (local_now.date() - timedelta(days=1)):
        return f"yesterday {time_part}"
    return f"{local_ts.strftime('%Y-%m-%d')} {time_part}"
