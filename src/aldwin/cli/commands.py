"""Slash command completer and command handlers for Aldwin's chat screen (`FR-5`)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Final, Protocol

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from rich.text import Text

from aldwin.cli.render import ChatRenderer
from aldwin.client import (
    MemorySummary,
    PersonaSummary,
    SkillsReport,
    ToolInfo,
)
from aldwin.protocol import DoneEvent, ErrorEvent

SLASH_COMMANDS: Final[tuple[tuple[str, str], ...]] = (
    ("/help", "Show commands and keyboard shortcuts"),
    ("/new", "Start a new conversation"),
    ("/model", "Switch the OpenAI chat model"),
    ("/persona", "Show current persona and file path"),
    ("/memory", "Show profile and recent notes"),
    ("/skills", "List installed skills"),
    ("/tools", "List built-in tools"),
    ("/exit", "Save and exit Aldwin"),
)

KEYBINDINGS_HELP: Final[tuple[tuple[str, str], ...]] = (
    ("Enter", "Send message"),
    ("Alt+Enter (Esc then Enter)", "Insert a new line"),
    ("Esc", "Stop the current answer or decline approval"),
    ("↑ / ↓", "Previous / next message in history"),
    ("Ctrl+R", "Search input history"),
    ("Ctrl+C twice / Ctrl+D", "Save and exit"),
)


class SlashCommandClient(Protocol):
    """Structural protocol satisfied by `AldwinClient` and test doubles."""

    @property
    def model(self) -> str: ...

    async def new_conversation(self) -> None: ...

    async def list_models(self) -> list[str]: ...

    async def set_model(self, name: str) -> DoneEvent | ErrorEvent: ...

    def persona_summary(self) -> PersonaSummary: ...

    def memory_summary(self) -> MemorySummary: ...

    def skills_report(self) -> SkillsReport: ...

    def tools_summary(self) -> list[ToolInfo]: ...


class SlashCompleter(Completer):
    """Prompt-toolkit completer for `/help`, `/new`, `/model`, etc. (`FR-5`)."""

    def get_completions(
        self,
        document: Document,
        complete_event: CompleteEvent,
    ) -> Iterable[Completion]:
        _ = complete_event
        before = document.text_before_cursor.lstrip()
        if not before.startswith("/") or " " in before or "\n" in before:
            return

        prefix = before.lower()
        for cmd, description in SLASH_COMMANDS:
            if cmd.startswith(prefix):
                yield Completion(
                    text=cmd,
                    start_position=-len(before),
                    display=cmd,
                    display_meta=description,
                )


def _render_help(renderer: ChatRenderer) -> None:
    console = renderer.console
    console.print(Text("Commands:", style="accent"))
    for cmd, desc in SLASH_COMMANDS:
        line = Text(f"  {cmd:<10} ")
        line.append(desc, style="muted")
        console.print(line)

    console.print(Text("Keys:", style="accent"))
    for key, desc in KEYBINDINGS_HELP:
        line = Text(f"  {key:<26} ")
        line.append(desc, style="muted")
        console.print(line)


async def _handle_model_command(
    arg: str,
    *,
    client: SlashCommandClient,
    renderer: ChatRenderer,
    prompt_line: Callable[[str], Awaitable[str]],
) -> None:
    console = renderer.console
    if arg:
        res = await client.set_model(arg)
        if isinstance(res, ErrorEvent):
            renderer.render_error(res.code, res.message)
        else:
            console.print(Text(f"✓ Switched model to {arg}.", style="ok"))
        return

    models = await client.list_models()
    if not models:
        models = [client.model]

    console.print(Text("Available OpenAI chat models:", style="muted"))
    for idx, name in enumerate(models, start=1):
        marker = " (current)" if name == client.model else ""
        console.print(Text(f"  {idx}. {name}{marker}"))

    try:
        raw_choice = await prompt_line(
            f"Choose model [1-{len(models)}, or Enter to cancel]: "
        )
    except (EOFError, KeyboardInterrupt):
        raw_choice = ""

    choice = raw_choice.strip()
    if not choice:
        console.print(Text(f"Model unchanged ({client.model}).", style="muted"))
        return

    if choice.isdigit():
        idx = int(choice)
        if 1 <= idx <= len(models):
            chosen = models[idx - 1]
        else:
            console.print(
                Text(
                    f"Invalid selection {choice!r}. "
                    f"Enter a number from 1 to {len(models)}.",
                    style="err",
                )
            )
            return
    else:
        chosen = choice

    res = await client.set_model(chosen)
    if isinstance(res, ErrorEvent):
        renderer.render_error(res.code, res.message)
    else:
        console.print(Text(f"✓ Switched model to {chosen}.", style="ok"))


def _render_persona(client: SlashCommandClient, renderer: ChatRenderer) -> None:
    console = renderer.console
    info = client.persona_summary()
    console.print(
        Text(
            f"Persona ({info.display_path} · "
            f"{info.size_chars}/{info.cap_chars} chars):",
            style="muted",
        ),
        soft_wrap=True,
    )
    console.print(Text(info.text))


def _render_memory(client: SlashCommandClient, renderer: ChatRenderer) -> None:
    console = renderer.console
    mem = client.memory_summary()
    console.print(
        Text(
            f"Profile: {mem.display_profile_path} "
            f"({mem.profile_size_chars}/{mem.profile_cap_chars} chars)",
            style="muted",
        ),
        soft_wrap=True,
    )
    if mem.profile_truncated:
        console.print(
            Text(
                f"Your profile is over {mem.profile_cap_chars:,} characters; "
                "only the first part is used",
                style="warn",
            )
        )
    console.print(
        Text(
            f"Notes in the last 7 days: {mem.notes_last_7_days}",
            style="muted",
        )
    )
    if mem.recent_notes:
        for note in mem.recent_notes:
            console.print(Text(f"  - [{note.date}] {note.text}"))
    else:
        console.print(Text("  (no recent notes)", style="muted"))


def _render_skills(client: SlashCommandClient, renderer: ChatRenderer) -> None:
    console = renderer.console
    report = client.skills_report()
    console.print(Text("Skills:", style="muted"))
    if report.valid:
        for skill in report.valid:
            line = Text("  ✓ ", style="ok")
            line.append(skill.name)
            line.append(f": {skill.description}", style="muted")
            console.print(line)
    else:
        console.print(Text("  (no valid skills installed)", style="muted"))

    for inv in report.invalid:
        err_line = Text("  ✗ ", style="err")
        err_line.append(f"{inv.name} (invalid: {inv.reason})", style="err")
        console.print(err_line)


def _render_tools(client: SlashCommandClient, renderer: ChatRenderer) -> None:
    console = renderer.console
    tools = client.tools_summary()
    console.print(Text("Built-in tools:", style="muted"))
    for tool_info in tools:
        if tool_info.name == "set_persona":
            continue
        line = Text(f"  • {tool_info.name:<13} ")
        suffix = " (always asks y/n)" if tool_info.requires_approval else ""
        line.append(f"{tool_info.description}{suffix}", style="muted")
        console.print(line)


async def handle_slash_command(
    line: str,
    *,
    client: SlashCommandClient,
    renderer: ChatRenderer,
    prompt_line: Callable[[str], Awaitable[str]],
    on_reset_tokens: Callable[[], None] | None = None,
) -> bool:
    """Execute a `/command` line (`FR-5`).

    Returns `True` when `/exit` is invoked and the chat loop should terminate.
    """
    stripped = line.strip()
    cmd_part, _, arg_part = stripped.partition(" ")
    cmd = cmd_part.lower()
    arg = arg_part.strip()

    if cmd == "/help":
        _render_help(renderer)
        return False

    if cmd == "/new":
        await client.new_conversation()
        if on_reset_tokens is not None:
            on_reset_tokens()
        renderer.render_new_conversation_divider()
        return False

    if cmd == "/model":
        await _handle_model_command(
            arg,
            client=client,
            renderer=renderer,
            prompt_line=prompt_line,
        )
        return False

    if cmd == "/persona":
        _render_persona(client, renderer)
        return False

    if cmd == "/memory":
        _render_memory(client, renderer)
        return False

    if cmd == "/skills":
        _render_skills(client, renderer)
        return False

    if cmd == "/tools":
        _render_tools(client, renderer)
        return False

    if cmd == "/exit":
        return True

    renderer.console.print(Text("Unknown command. Type /help.", style="warn"))
    return False
