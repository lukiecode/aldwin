"""Rich theme tokens, console factory, and display formatters for Aldwin's CLI."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import IO, Final

from rich.console import Console
from rich.theme import Theme

THEME: Final[Theme] = Theme(
    {
        "accent": "cyan",
        "muted": "grey50",
        "ok": "green",
        "warn": "yellow",
        "err": "red",
    }
)


def create_console(
    *,
    plain: bool = False,
    width: int | None = None,
    record: bool = False,
    file: IO[str] | None = None,
    force_terminal: bool | None = None,
) -> Console:
    """Create the shared Rich `Console` configured with Aldwin's theme (`FR-11`)."""
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
    """Format an ISO-8601 timestamp for the history divider (`FR-3`)."""
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


def short_session_id(session_id: str) -> str:
    """Extract a short 4-character identifier for the bottom toolbar (`FR-4`)."""
    cleaned = session_id.strip()
    if not cleaned:
        return "new"
    if "_" in cleaned:
        suffix = cleaned.rsplit("_", 1)[-1]
        if suffix:
            return suffix[:4]
    return cleaned[:4]
