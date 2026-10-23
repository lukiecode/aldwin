"""Collapse large pastes; expand on paste-again; resolve tokens on submit."""

from __future__ import annotations

import re
from typing import Final

PASTE_LINE_THRESHOLD: Final[int] = 3
PASTE_CHAR_THRESHOLD: Final[int] = 800
TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"\[Pasted text #(?P<n>\d+) \+(?P<lines>\d+) lines\]"
)


class PasteStore:
    """Collapse large pastes to tokens; expand on paste-again; resolve on submit."""

    def __init__(self) -> None:
        self._pastes: dict[int, str] = {}
        self._next_id: int = 1
        self._last_id: int | None = None
        self._expanded: set[int] = set()

    def should_collapse(self, text: str) -> bool:
        """True when text is large enough to become a paste token."""
        lines = text.count("\n") + (1 if text else 0)
        return lines >= PASTE_LINE_THRESHOLD or len(text) > PASTE_CHAR_THRESHOLD

    def collapse(self, text: str) -> str:
        """Store ``text`` and return a ``[Pasted text #N +L lines]`` token."""
        if not self.should_collapse(text):
            return text
        paste_id = self._next_id
        self._next_id += 1
        self._pastes[paste_id] = text
        self._last_id = paste_id
        line_count = text.count("\n") + 1
        return f"[Pasted text #{paste_id} +{line_count} lines]"

    def expand_last(self) -> str | None:
        """Return the full text of the last collapsed paste (for paste-again)."""
        if self._last_id is None:
            return None
        self._expanded.add(self._last_id)
        return self._pastes.get(self._last_id)

    def resolve(self, text: str) -> str:
        """Replace paste tokens with full stored text on submit."""

        def _repl(match: re.Match[str]) -> str:
            paste_id = int(match.group("n"))
            return self._pastes.get(paste_id, match.group(0))

        return TOKEN_RE.sub(_repl, text)
