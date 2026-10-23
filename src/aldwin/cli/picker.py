"""Shared inline ↑/↓ Enter Esc list for /model and similar choosers."""

from __future__ import annotations

from collections.abc import Sequence

from prompt_toolkit.application import Application
from prompt_toolkit.input import Input, PipeInput
from prompt_toolkit.key_binding import KeyBindings, KeyPressEvent
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.output import DummyOutput


async def pick_index(
    items: Sequence[str],
    *,
    default: int = 0,
    input_dev: Input | None = None,
    plain: bool = False,
) -> int | None:
    """Show an inline ❯ list; return selected index, or ``None`` on Esc.

    Uses a minimal prompt_toolkit Application so tests can drive it via PipeInput.
    PipeInput tests use DummyOutput; real TTYs keep the normal terminal output.
    """
    if not items:
        return None
    selected = max(0, min(default, len(items) - 1))
    result: dict[str, int | None] = {"value": None}

    def _render() -> list[tuple[str, str]]:
        fragments: list[tuple[str, str]] = []
        for idx, label in enumerate(items):
            if plain:
                marker = "> " if idx == selected else "  "
                style = ""
            else:
                marker = "❯ " if idx == selected else "  "
                style = "class:accent" if idx == selected else "class:muted"
            fragments.append((style, f"{marker}{label}\n"))
        return fragments

    kb = KeyBindings()

    @kb.add("up")
    @kb.add("c-p")
    def _up(_event: KeyPressEvent) -> None:
        nonlocal selected
        selected = (selected - 1) % len(items)

    @kb.add("down")
    @kb.add("c-n")
    def _down(_event: KeyPressEvent) -> None:
        nonlocal selected
        selected = (selected + 1) % len(items)

    @kb.add("enter")
    def _enter(event: KeyPressEvent) -> None:
        result["value"] = selected
        event.app.exit()

    @kb.add("escape", eager=True)
    def _esc(event: KeyPressEvent) -> None:
        result["value"] = None
        event.app.exit()

    # Digit shortcuts 1..n
    for digit in range(1, min(9, len(items)) + 1):

        @kb.add(str(digit))
        def _digit(event: KeyPressEvent, choice: int = digit - 1) -> None:
            result["value"] = choice
            event.app.exit()

    control = FormattedTextControl(_render, show_cursor=False)  # type: ignore[arg-type]
    app: Application[None] = Application(
        layout=Layout(Window(content=control)),
        key_bindings=kb,
        full_screen=False,
        input=input_dev,
        output=DummyOutput() if isinstance(input_dev, PipeInput) else None,
    )
    await app.run_async()
    return result["value"]
