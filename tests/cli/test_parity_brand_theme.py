"""Brand constants and theme / config persistence."""

from __future__ import annotations

from aldwin.cli.brand import ALDWIN_WORDMARK, TAGLINE, WORDMARK_FIRST_RUN_MAX_S
from aldwin.cli.theme import (
    ACCENT_HEX,
    clear_terminal_title,
    set_terminal_title,
    should_show_wordmark_splash,
)


def test_wordmark_and_accent() -> None:
    assert len(ALDWIN_WORDMARK) >= 4
    assert "personal assistant" in TAGLINE.lower()
    assert ACCENT_HEX.startswith("#")
    assert WORDMARK_FIRST_RUN_MAX_S > 0


def test_wordmark_splash_gates() -> None:
    assert should_show_wordmark_splash(plain=True, width=100) is False
    assert should_show_wordmark_splash(plain=False, width=40) is False


def test_terminal_title_osc_helpers() -> None:
    class Buf:
        def __init__(self) -> None:
            self.data = ""

        def write(self, s: str) -> int:
            self.data += s
            return len(s)

        def flush(self) -> None:
            pass

    buf = Buf()
    set_terminal_title(buf, "Aldwin · working…", plain=False)  # type: ignore[arg-type]
    assert "\x1b]0;" in buf.data
    assert "working" in buf.data
    clear_terminal_title(buf, plain=False)  # type: ignore[arg-type]
    assert "Aldwin" in buf.data

    buf2 = Buf()
    set_terminal_title(buf2, "x", plain=True)  # type: ignore[arg-type]
    assert buf2.data == ""
