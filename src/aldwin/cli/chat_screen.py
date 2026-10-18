"""Interactive chat loop, raw key listener (`Esc`/`Ctrl-C`), and approval prompt."""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator
from contextlib import AbstractAsyncContextManager, ExitStack, contextmanager
from pathlib import Path
from typing import Final, Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.input import Input, PipeInput, create_input
from prompt_toolkit.key_binding import KeyBindings, KeyPress, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput, Output
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from aldwin.cli.commands import (
    SlashCommandClient,
    SlashCompleter,
    handle_slash_command,
    render_shortcuts,
)
from aldwin.cli.render import ChatRenderer
from aldwin.cli.theme import (
    PLAIN_PROMPT_STYLE,
    PROMPT_STYLE,
    context_left_percent,
    create_console,
)
from aldwin.client import (
    BootstrapResult,
    open_in_process_client,
    pop_profile_truncation_warning,
)
from aldwin.protocol import (
    ApprovalAnswer,
    ApprovalEvent,
    DoneEvent,
    ErrorEvent,
    Event,
    HistoryEvent,
    StatusEvent,
    TextEvent,
    ToolCallEvent,
    ToolResultEvent,
)

ESCAPE_FLUSH_DELAY: Final[float] = 0.05
CTRL_C_QUIT_WINDOW: Final[float] = 2.0


class ChatClientProtocol(SlashCommandClient, Protocol):
    """Structural protocol satisfied by `AldwinClient` and `FakeClient`."""

    @property
    def session_id(self) -> str: ...

    @property
    def updated_at(self) -> str | None: ...

    async def connect(self) -> DoneEvent: ...

    async def history(self, limit: int = 20) -> HistoryEvent: ...

    def turn(self, text: str) -> AsyncGenerator[Event, None]: ...

    async def stop(self) -> DoneEvent | ErrorEvent: ...

    async def approve(
        self,
        approval_id: str,
        answer: ApprovalAnswer,
    ) -> DoneEvent | ErrorEvent: ...

    async def close(self) -> None: ...


def format_bottom_toolbar(
    model: str,
    session_id: str = "",
    used_tokens: int = 0,
    *,
    width: int | None = None,
) -> str:
    """Format the bottom status line (`<model> · <N>% context left` + hints)."""
    _ = session_id
    pct = context_left_percent(model, used_tokens)
    left = f" {model} · {pct}% context left"
    right = "? shortcuts · esc stop "
    if width is not None and width >= len(left) + len(right) + 4:
        gap = width - len(left) - len(right)
        return f"{left}{' ' * gap}{right}"
    return f"{left}   {right}"


async def read_approval_key(
    input_dev: Input,
    *,
    escape_delay: float = ESCAPE_FLUSH_DELAY,
) -> ApprovalAnswer:
    """Read a single `y`/`n`/`Enter`/`Esc` key for an approval prompt (`FR-8`)."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[ApprovalAnswer] = loop.create_future()
    flush_handle: asyncio.TimerHandle | None = None

    def _resolve(answer: ApprovalAnswer) -> None:
        if not future.done():
            future.set_result(answer)

    def _handle_keys(keys: list[KeyPress]) -> None:
        for kp in keys:
            if future.done():
                return
            lowered = kp.data.lower() if kp.data else ""
            if lowered == "y":
                _resolve("yes")
                return
            if lowered == "n" or kp.key in (
                Keys.Enter,
                Keys.ControlM,
                Keys.ControlJ,
                Keys.Escape,
                Keys.ControlC,
                Keys.ControlD,
            ):
                _resolve("no")
                return

    def _flush() -> None:
        try:
            flushed = input_dev.flush_keys()
        except Exception:
            _resolve("no")
            return
        _handle_keys(flushed)

    def _on_ready() -> None:
        nonlocal flush_handle
        if flush_handle is not None:
            flush_handle.cancel()
            flush_handle = None
        if input_dev.closed:
            _resolve("no")
            return
        try:
            keys = input_dev.read_keys()
        except Exception:
            _resolve("no")
            return
        _handle_keys(keys)
        if not future.done():
            flush_handle = loop.call_later(escape_delay, _flush)

    stack = ExitStack()
    try:
        stack.enter_context(input_dev.raw_mode())
        stack.enter_context(input_dev.attach(_on_ready))
    except (EOFError, NotImplementedError, OSError):
        stack.close()
        return "no"

    try:
        return await future
    finally:
        if flush_handle is not None:
            flush_handle.cancel()
        stack.close()


class TurnKeyListener:
    """Listens for `Esc` and `Ctrl-C` on `input_dev` while a turn is running (`FR-9`).

    Uses `input_dev.raw_mode()` + `input_dev.attach()` on the shared `asyncio`
    event loop and schedules a `50 ms` `flush_keys()` timer so bare `Esc` is
    flushed promptly without misinterpreting multi-byte arrow or `Alt` sequences
    (`OQ-2`, `OQ-3`).
    """

    def __init__(
        self,
        input_dev: Input,
        *,
        on_escape: Callable[[], Awaitable[None]],
        on_ctrl_c: Callable[[], Awaitable[None]],
        escape_delay: float = ESCAPE_FLUSH_DELAY,
    ) -> None:
        self._input = input_dev
        self._on_escape = on_escape
        self._on_ctrl_c = on_ctrl_c
        self._escape_delay = escape_delay

        self._stack: ExitStack | None = None
        self._flush_handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._paused = False

    async def _invoke(self, coro_fn: Callable[[], Awaitable[None]]) -> None:
        await coro_fn()

    def _spawn(self, coro_fn: Callable[[], Awaitable[None]]) -> None:
        task: asyncio.Task[None] = asyncio.create_task(self._invoke(coro_fn))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _process_keys(self, keys: list[KeyPress]) -> None:
        for kp in keys:
            if kp.key == Keys.Escape:
                self._spawn(self._on_escape)
            elif kp.key == Keys.ControlC:
                self._spawn(self._on_ctrl_c)

    def _flush_pending(self) -> None:
        self._flush_handle = None
        if self._paused or self._input.closed:
            return
        try:
            flushed = self._input.flush_keys()
        except Exception:
            return
        self._process_keys(flushed)

    def _on_input_ready(self) -> None:
        if self._paused or self._input.closed:
            return
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        try:
            keys = self._input.read_keys()
        except Exception:
            return
        self._process_keys(keys)
        try:
            loop = asyncio.get_running_loop()
            self._flush_handle = loop.call_later(
                self._escape_delay,
                self._flush_pending,
            )
        except RuntimeError:
            pass

    def _attach(self) -> None:
        if self._stack is not None:
            return
        stack = ExitStack()
        try:
            stack.enter_context(self._input.raw_mode())
            stack.enter_context(self._input.attach(self._on_input_ready))
        except (EOFError, NotImplementedError, OSError):
            stack.close()
            return
        self._stack = stack

    def _detach(self) -> None:
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        if self._stack is not None:
            self._stack.close()
            self._stack = None

    @contextmanager
    def paused(self) -> Iterator[None]:
        """Temporarily detach the listener while an approval key is read (`FR-8`)."""
        self._paused = True
        self._detach()
        try:
            yield
        finally:
            self._paused = False
            self._attach()

    async def __aenter__(self) -> TurnKeyListener:
        self._attach()
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        _ = exc_info
        self._detach()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


def _create_history(home: Path) -> History:
    history_path = home / "history"
    try:
        history_path.parent.mkdir(parents=True, exist_ok=True)
        return FileHistory(str(history_path))
    except OSError:
        return InMemoryHistory()


def build_prompt_session(
    home: Path,
    *,
    toolbar_fn: Callable[[], str],
    plain: bool = False,
    input_dev: Input | None = None,
    output_dev: Output | None = None,
) -> PromptSession[str]:
    """Create `PromptSession` with `FileHistory`, `SlashCompleter`, and keys."""
    kb = KeyBindings()

    @kb.add("escape", "enter")
    def _insert_newline(event: KeyPressEvent) -> None:
        event.current_buffer.insert_text("\n")

    @kb.add("c-j")
    def _insert_newline_ctrl_j(event: KeyPressEvent) -> None:
        event.current_buffer.insert_text("\n")

    @kb.add("c-m")
    def _handle_enter(event: KeyPressEvent) -> None:
        buf = event.current_buffer
        if buf.document.text_before_cursor.endswith("\\"):
            buf.delete_before_cursor(count=1)
            buf.insert_text("\n")
        else:
            buf.validate_and_handle()

    effective_output = output_dev
    if effective_output is None and isinstance(input_dev, PipeInput):
        effective_output = DummyOutput()

    return PromptSession(
        history=_create_history(home),
        completer=SlashCompleter(),
        key_bindings=kb,
        bottom_toolbar=toolbar_fn,
        style=PLAIN_PROMPT_STYLE if plain else PROMPT_STYLE,
        multiline=False,
        input=input_dev,
        output=effective_output,
    )


async def run_chat(
    boot: BootstrapResult,
    *,
    debug: bool = False,
    skip_welcome: bool = False,
    client: ChatClientProtocol | None = None,
    console: Console | None = None,
    input_dev: Input | None = None,
    output_dev: Output | None = None,
) -> int:
    """Run the main Aldwin interactive chat loop (`FR-3` to `FR-12`)."""
    _ = debug
    active_console = console or create_console(plain=boot.plain)
    renderer = ChatRenderer(active_console, plain=boot.plain)

    if client is None and input_dev is None and not sys.stdin.isatty():
        active_console.print(
            Text(
                "Aldwin requires an interactive terminal (TTY) to run chat.",
                style="muted",
            )
        )
        return 0

    try:
        active_input = input_dev or create_input()
    except (EOFError, NotImplementedError, OSError):
        active_console.print(
            Text(
                "Aldwin requires an interactive terminal (TTY) to run chat.",
                style="muted",
            )
        )
        return 0

    owns_input = input_dev is None

    if client is not None:
        return await _chat_loop(
            boot=boot,
            client=client,
            renderer=renderer,
            input_dev=active_input,
            output_dev=output_dev,
            owns_input=owns_input,
            skip_welcome=skip_welcome,
        )

    ctx: AbstractAsyncContextManager[ChatClientProtocol] = open_in_process_client(
        initial_model=boot.model,
        home=boot.home,
    )
    async with ctx as live_client:
        return await _chat_loop(
            boot=boot,
            client=live_client,
            renderer=renderer,
            input_dev=active_input,
            output_dev=output_dev,
            owns_input=owns_input,
            skip_welcome=skip_welcome,
        )


async def _chat_loop(
    *,
    boot: BootstrapResult,
    client: ChatClientProtocol,
    renderer: ChatRenderer,
    input_dev: Input,
    output_dev: Output | None,
    owns_input: bool,
    skip_welcome: bool = False,
) -> int:
    console = renderer.console
    last_context_tokens = 0
    last_ctrl_c_at = 0.0
    quit_requested = False

    def _reset_tokens() -> None:
        nonlocal last_context_tokens
        last_context_tokens = 0

    def _toolbar() -> str:
        return format_bottom_toolbar(
            client.model,
            client.session_id,
            last_context_tokens,
            width=console.width,
        )

    session = build_prompt_session(
        boot.home,
        toolbar_fn=_toolbar,
        plain=boot.plain,
        input_dev=input_dev,
        output_dev=output_dev,
    )

    async def _prompt_line(label: str) -> str:
        sub_output = DummyOutput() if isinstance(input_dev, PipeInput) else output_dev
        sub_session: PromptSession[str] = PromptSession(
            input=input_dev,
            output=sub_output,
            style=PLAIN_PROMPT_STYLE if boot.plain else PROMPT_STYLE,
        )
        return await sub_session.prompt_async(label)

    try:
        await client.connect()
        history_ev = await client.history(limit=20)
        if not skip_welcome:
            renderer.render_welcome(
                first_run=False,
                model=client.model,
                display_home=boot.display_home,
            )
        renderer.render_history(history_ev, updated_at=client.updated_at)

        profile_warn = pop_profile_truncation_warning()
        if profile_warn:
            console.print(Text(profile_warn, style="warn"))

        use_patch_stdout = not isinstance(input_dev, PipeInput) and sys.stdout.isatty()
        first_prompt = True

        while not quit_requested:
            if not first_prompt:
                console.print()
            first_prompt = False

            if not boot.plain and console.is_interactive:
                console.print(Rule(style="border", characters="─"))

            try:
                if use_patch_stdout:
                    with patch_stdout(raw=True):
                        user_text = await session.prompt_async("› ")
                else:
                    user_text = await session.prompt_async("› ")
            except KeyboardInterrupt:
                now = time.monotonic()
                if now - last_ctrl_c_at <= CTRL_C_QUIT_WINDOW:
                    break
                last_ctrl_c_at = now
                console.print(Text("Press Ctrl-C again to quit", style="muted"))
                continue
            except EOFError:
                break

            stripped_input = user_text.strip()
            if not stripped_input:
                continue

            console.print()
            last_ctrl_c_at = 0.0

            if stripped_input == "?":
                render_shortcuts(renderer)
                continue

            if stripped_input.startswith("/"):
                should_exit = await handle_slash_command(
                    user_text,
                    client=client,
                    renderer=renderer,
                    prompt_line=_prompt_line,
                    on_reset_tokens=_reset_tokens,
                )
                if should_exit:
                    break
                continue

            turn_renderer = renderer.start_turn()

            async def _on_turn_escape() -> None:
                await client.stop()

            async def _on_turn_ctrl_c() -> None:
                nonlocal last_ctrl_c_at, quit_requested
                now = time.monotonic()
                if now - last_ctrl_c_at <= CTRL_C_QUIT_WINDOW:
                    quit_requested = True
                    await client.stop()
                    return
                last_ctrl_c_at = now
                await client.stop()
                console.print(Text("Press Ctrl-C again to quit", style="muted"))

            try:
                async with TurnKeyListener(
                    input_dev,
                    on_escape=_on_turn_escape,
                    on_ctrl_c=_on_turn_ctrl_c,
                ) as listener:
                    async for ev in client.turn(user_text):
                        if isinstance(ev, TextEvent):
                            turn_renderer.on_text(ev.delta)
                        elif isinstance(ev, ToolCallEvent):
                            turn_renderer.on_tool_call(ev)
                        elif isinstance(ev, ToolResultEvent):
                            turn_renderer.on_tool_result(ev)
                        elif isinstance(ev, StatusEvent):
                            turn_renderer.on_status(ev.message)
                        elif isinstance(ev, ApprovalEvent):
                            turn_renderer.pause_for_approval(ev)
                            with listener.paused():
                                answer = await read_approval_key(input_dev)
                            await client.approve(ev.approval_id, answer)
                            turn_renderer.resolve_approval(ev, answer)
                        elif isinstance(ev, DoneEvent):
                            turn_tokens = turn_renderer.finish_done(ev)
                            if ev.usage is not None:
                                last_context_tokens = (
                                    ev.usage.input_tokens + ev.usage.output_tokens
                                )
                            else:
                                last_context_tokens += turn_tokens
                        elif isinstance(ev, ErrorEvent):
                            turn_renderer.finish_error(ev)
            finally:
                turn_renderer.close()

            post_warn = pop_profile_truncation_warning()
            if post_warn:
                console.print(Text(post_warn, style="warn"))

        await client.close()
        console.print(Text("Saved. See you.", style="muted"))
        return 0
    finally:
        if owns_input:
            input_dev.close()
