"""AldwinMarkdown basics: quotes, headings, compact code."""

from __future__ import annotations

from rich.console import Console

from aldwin.cli.markdown import AldwinMarkdown, render_markdown


def test_quote_rewrites_to_bar() -> None:
    console = Console(record=True, width=60, force_terminal=True)
    console.print(render_markdown("> hello"))
    text = console.export_text()
    assert "│" in text
    assert "hello" in text


def test_plain_returns_text() -> None:
    out = render_markdown("# Title", plain=True)
    assert str(out) == "# Title"


def test_aldwin_markdown_subclass() -> None:
    md = render_markdown("# Hi\n\n```py\nprint(1)\n```")
    assert isinstance(md, AldwinMarkdown)
