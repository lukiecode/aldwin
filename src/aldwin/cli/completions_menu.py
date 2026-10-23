"""Boxless slash / @ completion overlay above the input row."""

from __future__ import annotations

from prompt_toolkit.filters import FilterOrBool
from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.shortcuts import PromptSession


class SlashCompletionsMenu(CompletionsMenu):
    """Completions menu without a heavy border box; accent for the selected row."""

    def __init__(
        self,
        *,
        max_height: int = 8,
        scroll_offset: int = 1,
        extra_filter: FilterOrBool = True,
    ) -> None:
        super().__init__(
            max_height=max_height,
            scroll_offset=scroll_offset,
            extra_filter=extra_filter,
            display_arrows=False,
        )


def format_completion_row(
    *,
    selected: bool,
    name: str,
    description: str,
    plain: bool = False,
) -> StyleAndTextTuples:
    """One overlay row: glyph + name, muted description."""
    marker = "> " if plain else ("❯ " if selected else "  ")
    if plain:
        return [("", f"{marker}{name}  {description}")]
    if selected:
        return [
            ("class:accent", f"{marker}{name}"),
            ("class:muted", f"  {description}"),
        ]
    return [
        ("class:muted", f"{marker}{name}"),
        ("class:muted", f"  {description}"),
    ]


def install_slash_completions_menu(session: PromptSession[str]) -> None:
    """Replace the default boxed CompletionsMenu float with SlashCompletionsMenu."""
    from prompt_toolkit.filters import has_focus
    from prompt_toolkit.layout.containers import FloatContainer
    from prompt_toolkit.layout.menus import CompletionsMenu

    app = session.app
    root = app.layout.container
    default_buffer = session.default_buffer

    def _walk(node: object) -> None:
        if isinstance(node, FloatContainer):
            for fl in node.floats:
                content = fl.content
                if isinstance(content, CompletionsMenu) and not isinstance(
                    content, SlashCompletionsMenu
                ):
                    fl.content = SlashCompletionsMenu(
                        max_height=8,
                        scroll_offset=1,
                        extra_filter=has_focus(default_buffer),
                    )
        children = getattr(node, "get_children", None)
        if callable(children):
            for child in children():
                _walk(child)
        elif hasattr(node, "content"):
            inner = getattr(node, "content", None)
            if inner is not None and inner is not node:
                _walk(inner)

    _walk(root)
