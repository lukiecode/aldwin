"""Unit tests for `SlashCompleter` and slash command handlers (`FR-5`)."""

from __future__ import annotations

import io

from fakes.host import FakeClient
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from aldwin.cli.commands import SLASH_COMMANDS, SlashCompleter, handle_slash_command
from aldwin.cli.render import ChatRenderer
from aldwin.cli.theme import create_console


async def _noop_prompt(_label: str) -> str:
    return ""


async def test_slash_model_numbered_list_and_switch() -> None:
    """TC-10: /model prints numbered list, switches via number or direct name."""
    client = FakeClient(
        model="gpt-4o",
        available_models=["gpt-4o", "gpt-4.1", "o3-mini"],
    )
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    async def pick_second(_label: str) -> str:
        return "2"

    should_exit = await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=pick_second,
    )
    assert should_exit is False
    assert client.model == "gpt-4.1"
    assert client.model_switches == ["gpt-4.1"]

    text = console.export_text()
    assert "1. gpt-4o (current)" in text
    assert "2. gpt-4.1" in text
    assert "3. o3-mini" in text
    assert "✓ Switched model to gpt-4.1." in text

    # Direct "/model o3-mini" shortcut
    await handle_slash_command(
        "/model o3-mini",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert client.model == "o3-mini"
    assert client.model_switches == ["gpt-4.1", "o3-mini"]

    # Unknown model shows error
    await handle_slash_command(
        "/model unknown-xyz",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert "Use /model to pick another." in console.export_text()

    # Cancel on Enter and invalid number selection
    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert "Model unchanged" in console.export_text()

    async def pick_out_of_range(_label: str) -> str:
        return "99"

    await handle_slash_command(
        "/model",
        client=client,
        renderer=renderer,
        prompt_line=pick_out_of_range,
    )
    assert "Invalid selection '99'" in console.export_text()


async def test_slash_unknown_command() -> None:
    """TC-13: /unknown prints 'Unknown command. Type /help.' without starting a turn."""
    client = FakeClient()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    should_exit = await handle_slash_command(
        "/unknown",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert should_exit is False
    assert client.turns == []
    assert "Unknown command. Type /help." in console.export_text()


async def test_slash_completer_and_inspect_commands() -> None:
    """TC-19: SlashCompleter yields commands with meta; inspect commands render."""
    completer = SlashCompleter()
    all_items = list(completer.get_completions(Document("/"), CompleteEvent()))
    assert [c.text for c in all_items] == [cmd for cmd, _ in SLASH_COMMANDS]
    assert all(c.display_meta for c in all_items)

    m_items = list(completer.get_completions(Document("/m"), CompleteEvent()))
    assert [c.text for c in m_items] == ["/model", "/memory"]

    none_items = list(completer.get_completions(Document("hello"), CompleteEvent()))
    assert none_items == []

    client = FakeClient()
    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    renderer = ChatRenderer(console, plain=True)

    tokens_reset = False

    def reset_tokens() -> None:
        nonlocal tokens_reset
        tokens_reset = True

    for cmd in ("/help", "/new", "/persona", "/memory", "/skills", "/tools"):
        res = await handle_slash_command(
            cmd,
            client=client,
            renderer=renderer,
            prompt_line=_noop_prompt,
            on_reset_tokens=reset_tokens,
        )
        assert res is False

    exit_res = await handle_slash_command(
        "/exit",
        client=client,
        renderer=renderer,
        prompt_line=_noop_prompt,
    )
    assert exit_res is True
    assert client.new_conversations == 1
    assert tokens_reset is True

    text = console.export_text()
    assert "/help" in text and "Alt+Enter" in text
    assert "new conversation" in text
    assert "~/.aldwin/persona.md" in text
    assert "calm and direct assistant" in text
    assert "~/.aldwin/memory/profile.md" in text
    assert "Fola prefers short answers" in text
    assert "✓ using-aldwin: How to use Aldwin's memory, persona, and tools." in text
    assert "✗ broken-skill (invalid: missing description)" in text
    assert "web_search" in text
    assert "run_command" in text and "always asks y/n" in text
