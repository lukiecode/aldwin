"""Hand-authored Unicode wordmark and first-run / splash constants."""

from __future__ import annotations

from typing import Final

# Fits in ≥60 columns; block art only (no PNG / Kitty / Sixel).
ALDWIN_WORDMARK: Final[tuple[str, ...]] = (
    "  █████╗ ██╗     ██████╗ ██╗    ██╗██╗███╗   ██╗",
    " ██╔══██╗██║     ██╔══██╗██║    ██║██║████╗  ██║",
    " ███████║██║     ██║  ██║██║ █╗ ██║██║██╔██╗ ██║",
    " ██╔══██║██║     ██║  ██║██║███╗██║██║██║╚██╗██║",
    " ██║  ██║███████╗██████╔╝╚███╔███╔╝██║██║ ╚████║",
    " ╚═╝  ╚═╝╚══════╝╚═════╝  ╚══╝╚══╝ ╚═╝╚═╝  ╚═══╝",
)

ALDWIN_WORDMARK_ONE_LINE: Final[str] = "ALDWIN"

# First-run splash timing (skip when plain or reduced motion).
WORDMARK_COLUMN_REVEAL_S: Final[float] = 0.7
WORDMARK_LIGHT_SWEEP_S: Final[float] = 0.7
WORDMARK_TAGLINE_TYPE_S: Final[float] = 0.5
WORDMARK_FIRST_RUN_MAX_S: Final[float] = 1.8
WORDMARK_UPGRADE_MAX_S: Final[float] = 1.2

TAGLINE: Final[str] = "A personal assistant that runs on your computer."

WHATS_NEW_0_0_2: Final[tuple[str, ...]] = (
    "Safer file edits with clear diff approvals",
    "Nine default skills and an @ skill picker",
    "Live task list and collapsed tool rows",
)

# Curated recommendations for onboarding / model step.
CURATED_MODELS: Final[dict[str, str]] = {
    "fast": "gpt-4o-mini",
    "best": "gpt-4o",
    "cheapest": "gpt-4o-mini",
}

STARTER_PROMPTS: Final[tuple[str, ...]] = (
    "Find every TODO in this folder",
    "Summarise the README in this workspace",
    "What can you help me with?",
)

PRIMER_LINES: Final[tuple[str, ...]] = (
    "Type at the ❯ prompt below and press Enter to talk to Aldwin.",
    "Teal accent marks actions and highlights; muted text is secondary.",
    "Use / for commands and @ to pick a skill.",
)
