"""Banned assistant filler phrases used by voice fixture checks."""

from __future__ import annotations

from typing import Final

BANNED_VOICE_PHRASES: Final[tuple[str, ...]] = (
    "let me know if",
    "let's proceed",
    "feel free to ask",
    "if there's anything else",
    "please let me know",
)


def contains_banned_voice(text: str) -> list[str]:
    """Return banned phrases found in `text` (case-insensitive)."""
    lowered = text.lower()
    return [phrase for phrase in BANNED_VOICE_PHRASES if phrase in lowered]
