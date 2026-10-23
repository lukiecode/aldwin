"""Aldwin-flavoured markdown rendering for assistant scrollback."""

from __future__ import annotations

from typing import ClassVar

from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.markdown import CodeBlock, Heading, Markdown
from rich.syntax import Syntax
from rich.text import Text


class _AldwinHeading(Heading):
    """Left-aligned heading without a full-width rule slab."""

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        text = self.text.copy()
        text.justify = "left"
        yield text


class _AldwinCodeBlock(CodeBlock):
    """Fenced code as a compact syntax block (no padded slab)."""

    def __rich_console__(
        self, console: Console, options: ConsoleOptions
    ) -> RenderResult:
        code = str(self.text).rstrip()
        syntax = Syntax(
            code,
            self.lexer_name,
            theme=self.theme,
            word_wrap=True,
            padding=0,
            background_color="default",
        )
        yield syntax


class AldwinMarkdown(Markdown):
    """Markdown with left headings and compact fenced code."""

    elements: ClassVar[dict[str, type]] = {
        **Markdown.elements,
        "heading_open": _AldwinHeading,
        "fence": _AldwinCodeBlock,
        "code_block": _AldwinCodeBlock,
    }


def render_markdown(text: str, *, plain: bool = False) -> RenderableType:
    """Return plain Text or AldwinMarkdown for a markdown string."""
    cleaned = text.strip()
    if not cleaned:
        return Text("")
    if plain:
        return Text(cleaned)
    # Quote lines: rewrite leading `>` to `│` for scrollback chrome.
    rewritten = "\n".join(
        ("│ " + line[1:].lstrip() if line.startswith(">") else line)
        for line in cleaned.splitlines()
    )
    return AldwinMarkdown(rewritten)
