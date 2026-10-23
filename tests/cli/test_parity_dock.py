"""Docked prompt behaviour: Shift+Enter maps, footer states, slash menu install."""

from __future__ import annotations

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES
from prompt_toolkit.keys import Keys
from prompt_toolkit.layout.containers import FloatContainer
from prompt_toolkit.output import DummyOutput

from aldwin.cli.completions_menu import (
    SlashCompletionsMenu,
    install_slash_completions_menu,
)
from aldwin.cli.dock import (
    CTRL_C_QUIT_WINDOW_S,
    PLACEHOLDER,
    AldwinPromptSession,
    register_shift_enter_sequences,
)


def test_csi_u_shift_enter_maps_to_newline() -> None:
    register_shift_enter_sequences()
    assert ANSI_SEQUENCES.get("\x1b[13;2u") == Keys.ControlJ
    assert ANSI_SEQUENCES.get("\x1b[27;2;13~") == Keys.ControlJ


def test_placeholder_and_quit_window_constants() -> None:
    assert "Ask anything" in PLACEHOLDER
    assert CTRL_C_QUIT_WINDOW_S == 2.0


def test_aldwin_prompt_session_footer_states() -> None:
    session = AldwinPromptSession(footer_right=lambda: "gpt-4o · bar")
    assert "? for shortcuts" in session._toolbar_text()
    session.set_busy(True)
    assert "esc to interrupt" in session._toolbar_text()
    session.set_busy(False)
    session.set_footer("ctrl_c", left="Press Ctrl-C again to exit")
    assert "Press Ctrl-C again to exit" in session._toolbar_text()


def test_slash_completions_menu_replaces_default() -> None:
    with create_pipe_input() as inp:
        session = AldwinPromptSession(input=inp, output=DummyOutput())
        install_slash_completions_menu(session)
        found: list[str] = []

        def walk(node: object) -> None:
            if isinstance(node, FloatContainer):
                for fl in node.floats:
                    found.append(type(fl.content).__name__)
            get_children = getattr(node, "get_children", None)
            if callable(get_children):
                for child in get_children():
                    walk(child)
            content = getattr(node, "content", None)
            if content is not None and content is not node:
                walk(content)

        walk(session.app.layout.container)
        assert "SlashCompletionsMenu" in found
        assert "CompletionsMenu" not in found
        assert any(isinstance(n, type) for n in [SlashCompletionsMenu])
