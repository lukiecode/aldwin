"""Rich render tests for the session checklist view."""

from __future__ import annotations

import io

from aldwin.cli.theme import create_console
from aldwin.cli.todo_view import TodoView, TodoViewState, render_todo_block
from aldwin.protocol import TodoItem, TodoUpdatedEvent


def _state() -> TodoViewState:
    return TodoViewState(
        items=[
            TodoItem(text="List downloads", status="done"),
            TodoItem(text="Rename invoices", status="in_progress"),
            TodoItem(text="Summarise contracts", status="pending"),
            TodoItem(text="Delete duplicates", status="dropped"),
        ],
        done=2,
        total=4,
    )


def test_todo_view_unicode_symbols() -> None:
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    text = console.export_text(clear=False)
    console.print(render_todo_block(_state(), plain=False, unicode=True))
    out = console.export_text()
    assert "Tasks 2/4" in out
    assert "✔" in out
    assert "▶" in out
    assert "☐" in out
    assert "✘" in out
    del text


def test_todo_view_plain_ascii() -> None:
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf, plain=True)
    console.print(render_todo_block(_state(), plain=True, unicode=False))
    out = console.export_text()
    assert "[x]" in out
    assert "[>]" in out
    assert "[ ]" in out
    assert "[-]" in out
    assert "✔" not in out


def test_todo_view_collapse() -> None:
    state = TodoViewState(
        items=[
            TodoItem(text="A", status="done"),
            TodoItem(text="B", status="done"),
            TodoItem(text="C", status="done"),
            TodoItem(text="D", status="done"),
            TodoItem(text="E", status="dropped"),
        ],
        done=5,
        total=5,
    )
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    console.print(render_todo_block(state, plain=False, unicode=True))
    out = console.export_text()
    assert "Tasks · 4 done, 1 dropped" in out


def test_todo_view_strips_ansi_and_bidi() -> None:
    dirty = TodoViewState(
        items=[
            TodoItem(text="\x1b[2J\u202eInjected", status="pending"),
        ],
        done=0,
        total=1,
    )
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    console.print(render_todo_block(dirty, plain=False, unicode=True))
    out = console.export_text()
    assert "\x1b" not in out
    assert "\u202e" not in out
    assert "Injected" in out


def test_todo_view_empty_message() -> None:
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    view = TodoView(plain=True)
    view.print_current(console)
    assert "No task list in this session" in console.export_text()


def test_todo_view_toolbar_label() -> None:
    view = TodoView()
    view.state = _state()
    assert view.toolbar_label() == "Tasks 2/4"
    view.state = TodoViewState(
        items=[TodoItem(text="A", status="done")],
        done=1,
        total=1,
    )
    assert view.toolbar_label() is None


def test_todo_view_from_event() -> None:
    event = TodoUpdatedEvent(
        re="r1",
        items=[TodoItem(text="A", status="pending")],
        done=0,
        total=1,
    )
    state = TodoViewState.from_event(event)
    assert state.total == 1
    assert state.items[0].text == "A"


def test_todo_view_freeze_stops_live_keeps_state() -> None:
    buf = io.StringIO()
    console = create_console(width=80, record=True, file=buf)
    view = TodoView(plain=False, unicode=True)
    # Force non-interactive so apply_event prints instead of starting Live.
    console.is_interactive = False  # type: ignore[misc]
    view.apply_event(
        TodoUpdatedEvent(
            re="r1",
            items=[TodoItem(text="A", status="in_progress")],
            done=0,
            total=1,
        ),
        console,
    )
    assert view.state is not None
    view.freeze()
    assert view.live_active is False
    assert view.state.total == 1
    assert view.toolbar_label() == "Tasks 0/1"
