"""Interactive chat loop, raw key listener (`Esc`/`Ctrl-C`), and approval prompt."""

from __future__ import annotations

import asyncio
import select
import sys
import time
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager, ExitStack, contextmanager
from pathlib import Path
from types import TracebackType
from typing import Final, Protocol

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory, History, InMemoryHistory
from prompt_toolkit.input import Input, PipeInput, create_input
from prompt_toolkit.input.typeahead import store_typeahead
from prompt_toolkit.key_binding import KeyBindings, KeyPress, KeyPressEvent
from prompt_toolkit.keys import Keys
from prompt_toolkit.output import DummyOutput, Output
from prompt_toolkit.patch_stdout import patch_stdout
from rich.console import Console
from rich.rule import Rule
from rich.text import Text

from aldwin.cli.commands import (
    SlashCommandClient,
    handle_slash_command,
    render_shortcuts,
)
from aldwin.cli.completion import RefreshableCompleter, build_chat_completer
from aldwin.cli.render import ChatRenderer
from aldwin.cli.skill_completer import SkillCache, SkillLexer, extract_skill_tokens
from aldwin.cli.theme import (
    PLAIN_PROMPT_STYLE,
    PROMPT_STYLE,
    context_left_percent,
    create_console,
)
from aldwin.client import (
    BootstrapResult,
    ProtocolError,
    open_in_process_client,
    pop_profile_truncation_warning,
    sanitize_display_text,
)
from aldwin.protocol import (
    ApprovalAnswer,
    ApprovalEvent,
    DoneEvent,
    ErrorEvent,
    Event,
    HistoryEvent,
    NoticeEvent,
    SkillsEvent,
    SkillsUsedEvent,
    StatusEvent,
    TextEvent,
    TodoUpdatedEvent,
    ToolCallEvent,
    ToolResultEvent,
)

ESCAPE_FLUSH_DELAY: Final[float] = 0.05


def _render_skills_used(console: Console, event: SkillsUsedEvent) -> None:
    """Print dim `using …` and yellow drop warnings from `skills.used`."""
    if event.names:
        names = [sanitize_display_text(n) for n in event.names]
        console.print(Text("using " + ", ".join(names), style="muted"))
    for dropped in event.dropped:
        name = sanitize_display_text(dropped.name)
        if dropped.reason == "unknown":
            msg = f"@{name} isn't a skill any more, sent as text"
        else:
            msg = f"Only 3 skills per message; @{name} ignored"
        console.print(Text(msg, style="warn"))


def _wake_pending_pipe_input(input_dev: Input) -> None:
    """Drain a pipe that already holds bytes when a reader attaches.

    ``add_reader`` wakes up when a pipe *becomes* readable. The chat tests write
    the first line before the prompt is listening, so those bytes are already
    in the pipe. A busy CI runner can miss that, and ``prompt_async`` then
    waits until the job is killed. If ``select`` can see the bytes, schedule
    one read as soon as the reader attaches.
    """
    if not isinstance(input_dev, PipeInput):
        return
    if getattr(input_dev, "_aldwin_wakes_pending", False):
        return

    original_attach = input_dev.attach

    def attach(callback: Callable[[], None]):
        context = original_attach(callback)

        class _DrainOnEnter:
            def __enter__(self) -> None:
                context.__enter__()
                try:
                    fd = input_dev.fileno()
                    ready, _, _ = select.select([fd], [], [], 0)
                except (OSError, ValueError):
                    return
                if not ready:
                    return
                try:
                    asyncio.get_running_loop().call_soon(callback)
                except RuntimeError:
                    return

            def __exit__(
                self,
                exc_type: type[BaseException] | None,
                exc: BaseException | None,
                tb: TracebackType | None,
            ) -> bool | None:
                return context.__exit__(exc_type, exc, tb)

        return _DrainOnEnter()

    setattr(input_dev, "attach", attach)
    setattr(input_dev, "_aldwin_wakes_pending", True)


CTRL_C_QUIT_WINDOW: Final[float] = 2.0
DECLINE_REASON_PROMPT: Final[str] = "  why? (Enter to skip): "


class ChatClientProtocol(SlashCommandClient, Protocol):
    """Structural protocol satisfied by `AldwinClient` and `FakeClient`."""

    @property
    def session_id(self) -> str: ...

    @property
    def updated_at(self) -> str | None: ...

    async def connect(self) -> DoneEvent: ...

    async def history(self, limit: int = 20) -> HistoryEvent: ...

    async def todo_get(self) -> TodoUpdatedEvent: ...

    def turn(
        self,
        text: str,
        *,
        skills: Sequence[str] = (),
    ) -> AsyncGenerator[Event, None]: ...

    async def skills_list(self) -> SkillsEvent: ...

    async def stop(self) -> DoneEvent | ErrorEvent: ...

    async def approve(
        self,
        approval_id: str,
        answer: ApprovalAnswer,
        *,
        digest: str | None = None,
        reason: str | None = None,
    ) -> DoneEvent | ErrorEvent: ...

    async def close(self) -> None: ...


def format_bottom_toolbar(
    model: str,
    session_id: str = "",
    used_tokens: int = 0,
    *,
    width: int | None = None,
    todo_label: str | None = None,
) -> str:
    """Format the bottom status line (`<model> · <N>% context left` + hints)."""
    _ = session_id
    pct = context_left_percent(model, used_tokens)
    todo_bit = f" · {todo_label}" if todo_label else ""
    left = f" {model} · {pct}% context left{todo_bit}"
    right = "? shortcuts · esc stop "
    if width is not None and width >= len(left) + len(right) + 4:
        gap = width - len(left) - len(right)
        return f"{left}{' ' * gap}{right}"
    return f"{left}   {right}"


async def read_approval_key(
    input_dev: Input,
    *,
    grantable: bool = False,
    on_expand: Callable[[], None] | None = None,
    escape_delay: float = ESCAPE_FLUSH_DELAY,
) -> ApprovalAnswer:
    """Read `y`/`a`/`n`/`Enter`/`Esc`/`Ctrl-O` for an approval prompt."""
    answer, _explicit_n = await _read_approval_key_detail(
        input_dev,
        grantable=grantable,
        on_expand=on_expand,
        escape_delay=escape_delay,
    )
    return answer


async def _read_approval_key_detail(
    input_dev: Input,
    *,
    grantable: bool = False,
    on_expand: Callable[[], None] | None = None,
    escape_delay: float = ESCAPE_FLUSH_DELAY,
) -> tuple[ApprovalAnswer, bool]:
    """Return `(answer, pressed_explicit_n)` for an approval prompt."""
    loop = asyncio.get_running_loop()
    future: asyncio.Future[tuple[ApprovalAnswer, bool]] = loop.create_future()
    flush_handle: asyncio.TimerHandle | None = None

    def _resolve(answer: ApprovalAnswer, explicit_n: bool = False) -> None:
        if not future.done():
            future.set_result((answer, explicit_n))

    def _keep_unread(unread: list[KeyPress]) -> None:
        # One read can contain the answer and the next line. The next prompt
        # picks up whatever this prompt did not use.
        if unread:
            store_typeahead(input_dev, unread)

    def _handle_keys(keys: list[KeyPress]) -> None:
        for index, kp in enumerate(keys):
            if future.done():
                _keep_unread(keys[index:])
                return
            if kp.key == Keys.ControlO:
                if on_expand is not None:
                    on_expand()
                continue
            lowered = kp.data.lower() if kp.data else ""
            if lowered == "y":
                _resolve("yes", False)
                _keep_unread(keys[index + 1 :])
                return
            if lowered == "a" and grantable:
                _resolve("yes_session", False)
                _keep_unread(keys[index + 1 :])
                return
            if lowered == "n":
                _resolve("no", True)
                _keep_unread(keys[index + 1 :])
                return
            if kp.key in (
                Keys.Enter,
                Keys.ControlM,
                Keys.ControlJ,
                Keys.Escape,
                Keys.ControlC,
                Keys.ControlD,
            ):
                _resolve("no", False)
                _keep_unread(keys[index + 1 :])
                return

    def _flush() -> None:
        try:
            flushed = input_dev.flush_keys()
        except Exception:
            _resolve("no", False)
            return
        _handle_keys(flushed)

    def _on_ready() -> None:
        nonlocal flush_handle
        if flush_handle is not None:
            flush_handle.cancel()
            flush_handle = None
        if input_dev.closed:
            _resolve("no", False)
            return
        try:
            keys = input_dev.read_keys()
        except Exception:
            _resolve("no", False)
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
        return "no", False

    try:
        return await future
    finally:
        if flush_handle is not None:
            flush_handle.cancel()
        stack.close()


class TurnKeyListener:
    """Listens for `Esc` and `Ctrl-C` on `input_dev` while a turn is running.

    Uses `input_dev.raw_mode()` + `input_dev.attach()` on the shared `asyncio`
    event loop and schedules a `50 ms` `flush_keys()` timer so bare `Esc` is
    flushed promptly without misinterpreting multi-byte arrow or `Alt` sequences.
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
        """Temporarily detach the listener while an approval key is read."""
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
    completer: object | None = None,
    lexer: object | None = None,
) -> PromptSession[str]:
    """Create `PromptSession` with `FileHistory`, completer, and keys."""
    from prompt_toolkit.completion import Completer
    from prompt_toolkit.lexers import Lexer

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

    active_completer: Completer | None
    if isinstance(completer, Completer):
        active_completer = completer
    else:
        active_completer = build_chat_completer(
            SkillCache(items=[]),
            enable_skills=True,
        )

    active_lexer = lexer if isinstance(lexer, Lexer) else None

    return PromptSession(
        history=_create_history(home),
        completer=active_completer,
        lexer=active_lexer,
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
    """Run the main Aldwin interactive chat loop."""
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
        workspace=boot.workspace,
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
    _wake_pending_pipe_input(input_dev)
    console = renderer.console
    last_context_tokens = 0
    last_ctrl_c_at = 0.0
    quit_requested = False

    def _reset_tokens() -> None:
        nonlocal last_context_tokens
        last_context_tokens = 0
        renderer.clear_todo()

    def _toolbar() -> str:
        return format_bottom_toolbar(
            client.model,
            client.session_id,
            last_context_tokens,
            width=console.width,
            todo_label=renderer.todo_view.toolbar_label(),
        )

    skill_cache = SkillCache(items=[])
    skill_lexer = SkillLexer(skill_cache)
    chat_completer = RefreshableCompleter(
        build_chat_completer(skill_cache, enable_skills=True)
    )
    session = build_prompt_session(
        boot.home,
        toolbar_fn=_toolbar,
        plain=boot.plain,
        input_dev=input_dev,
        output_dev=output_dev,
        completer=chat_completer,
        lexer=skill_lexer,
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
        hello = await client.connect()
        enable_skills = hello.v >= 2
        chat_completer.inner = build_chat_completer(
            skill_cache, enable_skills=enable_skills
        )
        if not enable_skills:
            session.lexer = None
        history_ev = await client.history(limit=20)
        if not skip_welcome:
            renderer.render_welcome(
                first_run=False,
                model=client.model,
                display_home=boot.display_home,
                display_workspace=boot.display_workspace or None,
            )
        renderer.render_history(history_ev, updated_at=client.updated_at)
        if enable_skills:
            try:
                catalog = await client.skills_list()
                skill_cache.replace(catalog.items)
            except (ProtocolError, AttributeError) as exc:
                console.print(Text(f"Skill list unavailable: {exc}", style="warn"))
                chat_completer.inner = build_chat_completer(
                    skill_cache, enable_skills=False
                )
                session.lexer = None
        try:
            from aldwin.cli.todo_view import TodoViewState

            todo_ev = await client.todo_get()
            if todo_ev.total > 0:
                renderer.todo_view.state = TodoViewState.from_event(todo_ev)
        except ProtocolError as exc:
            console.print(Text(str(exc), style="warn"))

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
            picked = extract_skill_tokens(user_text, skill_cache.known_names())

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
                    async for ev in client.turn(user_text, skills=picked):
                        if isinstance(ev, SkillsUsedEvent):
                            _render_skills_used(console, ev)
                        elif isinstance(ev, SkillsEvent):
                            skill_cache.replace(ev.items)
                        elif isinstance(ev, TextEvent):
                            turn_renderer.on_text(ev.delta)
                        elif isinstance(ev, TodoUpdatedEvent):
                            turn_renderer.pause_for_todo()
                            renderer.on_todo_updated(ev)
                        elif isinstance(ev, ToolCallEvent):
                            turn_renderer.on_tool_call(ev)
                        elif isinstance(ev, ToolResultEvent):
                            turn_renderer.on_tool_result(ev)
                        elif isinstance(ev, StatusEvent):
                            turn_renderer.on_status(ev.message)
                        elif isinstance(ev, NoticeEvent):
                            turn_renderer.on_status(ev.message)
                        elif isinstance(ev, ApprovalEvent):
                            current_ev = ev
                            turn_renderer.pause_for_approval(current_ev)
                            reason: str | None = None
                            with listener.paused():
                                answer, explicit_n = await _read_approval_key_detail(
                                    input_dev,
                                    grantable=current_ev.grantable,
                                    on_expand=lambda: (
                                        turn_renderer.expand_approval_panel(current_ev)
                                    ),
                                )
                                # Collapse the panel before the reason prompt so the
                                # Diff box is not still glowing while the user types.
                                turn_renderer.resolve_approval(ev, answer)
                                if answer == "no" and explicit_n:
                                    try:
                                        raw_reason = await _prompt_line(
                                            DECLINE_REASON_PROMPT
                                        )
                                        cleaned_reason = raw_reason.strip()
                                        if (
                                            cleaned_reason
                                            and not cleaned_reason.startswith("/")
                                        ):
                                            reason = cleaned_reason[:280]
                                    except (EOFError, KeyboardInterrupt):
                                        reason = None
                            await client.approve(
                                ev.approval_id,
                                answer,
                                digest=ev.digest or None,
                                reason=reason,
                            )
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
