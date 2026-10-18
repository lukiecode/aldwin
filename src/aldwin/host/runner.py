"""Turn runner, request scope, approval broker, and host loop for Aldwin."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Sequence
from types import TracebackType
from typing import Any
from uuid import uuid4

from agentling import (
    Agent,
    AgentSession,
    FinalEvent,
    StepEvent,
    TextDelta,
)
from agentling import (
    ToolCallEvent as AgentlingToolCallEvent,
)
from agentling import (
    ToolResultEvent as AgentlingToolResultEvent,
)
from agentling.memory import FinalStep
from agentling.models import Usage

from aldwin.core.agent import (
    build_agent,
    new_session,
    restore_session,
    switch_session_model,
)
from aldwin.core.models import RetryingModel, classify_model_error
from aldwin.core.settings import load_config, load_secrets, update_config_model
from aldwin.core.tools import RememberTool, summarize_tool_result
from aldwin.host.in_process import InProcessTransport
from aldwin.host.sessions import (
    MAX_PREVIEW_CHARS,
    SessionStore,
    StoredSession,
    shorten_one_line,
)
from aldwin.protocol import (
    PROTOCOL_VERSION,
    ApprovalAnswer,
    ApprovalEvent,
    ApproveRequest,
    DoneEvent,
    DoneReason,
    ErrorCode,
    ErrorEvent,
    Event,
    HelloRequest,
    HistoryEvent,
    HistoryRequest,
    Request,
    SetModelRequest,
    StatusEvent,
    StopRequest,
    TextEvent,
    TokenUsage,
    ToolCallEvent,
    ToolResultEvent,
    TurnRequest,
)

logger = logging.getLogger("aldwin.host.runner")

AskFn = Callable[[str, str], Awaitable[bool]]
AgentBuilder = Callable[[str, AskFn], Agent]
EventSender = Callable[[Event], Awaitable[None]]


def format_args_preview(arguments: dict[str, Any]) -> str:
    """Format tool call arguments as a single line capped at 120 characters."""
    if not arguments:
        return ""
    if "new_text" in arguments and isinstance(arguments["new_text"], str):
        raw_text = arguments["new_text"].strip()
        line_count = len(raw_text.splitlines()) if raw_text else 0
        unit = "line" if line_count == 1 else "lines"
        return f"persona.md · {line_count} {unit}"
    raw = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    return shorten_one_line(raw, MAX_PREVIEW_CHARS)


def format_tool_summary(content: str) -> str:
    """Format the first non-empty line of tool output capped at 120 characters."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped:
            return shorten_one_line(stripped, MAX_PREVIEW_CHARS)
    return ""


def _to_token_usage(usage: Usage | None) -> TokenUsage | None:
    """Convert Agentling `Usage` to protocol `TokenUsage`."""
    if usage is None:
        return None
    return TokenUsage(
        input_tokens=max(0, usage.input_tokens),
        output_tokens=max(0, usage.output_tokens),
    )


def classify_turn_error(exc: BaseException) -> tuple[ErrorCode, str]:
    """Map an Agentling or OpenAI exception to a protocol `(ErrorCode, message)`."""
    return classify_model_error(exc)


class RequestScope:
    """Enforces that every request `id` receives exactly one terminal event."""

    def __init__(self, req_id: str, send_event: EventSender) -> None:
        self.req_id = req_id
        self._send_event = send_event
        self._terminated = False

    @property
    def terminated(self) -> bool:
        """Return `True` once `done()` or `error()` has been emitted."""
        return self._terminated

    async def emit(self, event: Event) -> None:
        """Emit a non-terminal event carrying `re == self.req_id`."""
        if self._terminated:
            raise RuntimeError(
                f"Cannot emit {event.kind!r} after terminal event for {self.req_id!r}."
            )
        if isinstance(event, (DoneEvent, ErrorEvent)):
            raise RuntimeError("Use `done()` or `error()` for terminal events.")
        if event.re != self.req_id:
            raise ValueError(
                f"Event `re` ({event.re!r}) must match request id ({self.req_id!r})."
            )
        await self._send_event(event)

    async def done(
        self,
        reason: DoneReason,
        usage: TokenUsage | None = None,
    ) -> None:
        """Emit the single terminal `DoneEvent` for this request."""
        if self._terminated:
            raise RuntimeError(
                f"Terminal event already emitted for request {self.req_id!r}."
            )
        self._terminated = True
        await self._send_event(DoneEvent(re=self.req_id, reason=reason, usage=usage))

    async def error(self, code: ErrorCode, message: str) -> None:
        """Emit the single terminal `ErrorEvent` for this request."""
        if self._terminated:
            raise RuntimeError(
                f"Terminal event already emitted for request {self.req_id!r}."
            )
        self._terminated = True
        await self._send_event(ErrorEvent(re=self.req_id, code=code, message=message))

    async def __aenter__(self) -> RequestScope:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        if exc is not None:
            if isinstance(exc, asyncio.CancelledError):
                return False
            logger.exception("Unhandled error in request %s", self.req_id, exc_info=exc)
            if not self._terminated:
                await self.error(
                    "internal",
                    "Something went wrong. Details are in ~/.aldwin/logs/aldwin.log.",
                )
            return True
        if not self._terminated:
            raise RuntimeError(
                f"Request {self.req_id!r} exited without emitting a terminal event."
            )
        return False


class ApprovalBroker:
    """Tracks pending `asyncio.Future` objects for tool/persona approvals."""

    def __init__(self, send_event: EventSender) -> None:
        self._send_event = send_event
        self._pending: dict[str, asyncio.Future[bool]] = {}
        self._lock = asyncio.Lock()

    @property
    def has_pending(self) -> bool:
        """Return `True` if at least one approval is waiting for an answer."""
        return bool(self._pending)

    async def ask(self, re: str, summary: str, detail: str = "") -> bool:
        """Emit `ApprovalEvent` one at a time and await the door's `yes`/`no` answer."""
        async with self._lock:
            approval_id = f"ap_{uuid4().hex[:12]}"
            loop = asyncio.get_running_loop()
            future: asyncio.Future[bool] = loop.create_future()
            self._pending[approval_id] = future
            try:
                await self._send_event(
                    ApprovalEvent(
                        re=re,
                        approval_id=approval_id,
                        summary=summary,
                        detail=detail,
                    )
                )
                return await future
            finally:
                self._pending.pop(approval_id, None)

    def resolve(self, approval_id: str, answer: ApprovalAnswer) -> bool:
        """Resolve a pending approval future; return `False` if unknown."""
        future = self._pending.pop(approval_id, None)
        if future is None or future.done():
            return False
        future.set_result(answer == "yes")
        return True

    def cancel_all(self) -> None:
        """Resolve all pending approvals as `False` (`no`), used on `stop`."""
        for approval_id in list(self._pending.keys()):
            future = self._pending.pop(approval_id, None)
            if future is not None and not future.done():
                future.set_result(False)


def _default_agent_builder(model_name: str, ask: AskFn) -> Agent:
    """Build Aldwin's `Agent` from home configuration, skills, and context (`FR-1`)."""
    config = load_config().model_copy(update={"model": model_name})
    secrets = load_secrets()
    return build_agent(config, secrets, ask)


class AldwinHost:
    """In-process pass that receives protocol requests and drives `AgentSession`."""

    def __init__(
        self,
        transport: InProcessTransport,
        session_store: SessionStore | None = None,
        agent_builder: AgentBuilder | None = None,
        *,
        initial_model: str | None = None,
        available_models: Sequence[str] | Callable[[], Sequence[str]] | None = None,
        persist_model: bool = True,
    ) -> None:
        self._transport = transport
        self._session_store = session_store or SessionStore()
        self._agent_builder = agent_builder or _default_agent_builder
        self._available_models = available_models
        self._persist_model = persist_model

        default_model = initial_model or load_config().model
        self._stored_session: StoredSession = self._session_store.load_latest(
            default_model=default_model
        )
        self._model: str = initial_model or self._stored_session.meta.model

        self._welcomed = False
        self._active_turn_id: str | None = None
        self._active_turn_scope: RequestScope | None = None
        self._last_approval_end_time: float = 0.0
        self._turn_task: asyncio.Task[None] | None = None
        self._serve_task: asyncio.Task[None] | None = None

        self._approvals = ApprovalBroker(self._transport.send_event)
        self._agent: Agent = self._build_and_attach_agent(self._model)
        self._agent_session: AgentSession = restore_session(
            self._agent,
            self._stored_session.memory.dump_json(),
        )

        self._transport.set_new_conversation_handler(self.new_conversation)

    def _build_and_attach_agent(self, model_name: str) -> Agent:
        """Build an `Agent` and attach the host's retry and session callbacks."""
        agent = self._agent_builder(model_name, self.ask)
        if isinstance(agent.model, RetryingModel) and agent.model.on_retry is None:
            agent.model.on_retry = self.on_retry
        for tool_obj in agent._base_tools.values():
            if (
                isinstance(tool_obj, RememberTool)
                and tool_obj._session_id_getter is None
            ):
                tool_obj._session_id_getter = lambda: self._stored_session.meta.id
        return agent

    async def on_retry(
        self,
        attempt: int,
        max_retries: int,
        _exc: BaseException,
    ) -> None:
        """Emit a non-terminal `StatusEvent` when the model retries (`FR-8`)."""
        scope = self._active_turn_scope
        if scope is not None and not scope.terminated:
            await scope.emit(
                StatusEvent(
                    re=scope.req_id,
                    message=f"OpenAI is busy, retrying ({attempt} of {max_retries})…",
                )
            )

    @property
    def model(self) -> str:
        """Return the currently configured model name."""
        return self._model

    @property
    def stored_session(self) -> StoredSession:
        """Return the active `StoredSession`."""
        return self._stored_session

    async def start(self) -> None:
        """Start the background request dispatch loop."""
        if self._serve_task is None or self._serve_task.done():
            self._serve_task = asyncio.create_task(self._serve_loop())

    async def stop_host(self) -> None:
        """Cancel any active turn, close the transport, and stop the dispatch loop."""
        self._approvals.cancel_all()
        if self._turn_task is not None and not self._turn_task.done():
            self._agent_session.interrupt()
            self._turn_task.cancel()
            await asyncio.gather(self._turn_task, return_exceptions=True)
        await self._transport.close()
        if self._serve_task is not None and not self._serve_task.done():
            await asyncio.gather(self._serve_task, return_exceptions=True)

    async def ask(self, summary: str, detail: str = "") -> bool:
        """Injected into tools so they can request user approval via the host."""
        re_id = self._active_turn_id or "req_turn"
        try:
            return await self._approvals.ask(re=re_id, summary=summary, detail=detail)
        finally:
            self._last_approval_end_time = time.monotonic()

    async def new_conversation(self) -> None:
        """Close the current session file and start a fresh conversation (`FR-14`)."""
        self._approvals.cancel_all()
        if self._turn_task is not None and not self._turn_task.done():
            self._agent_session.interrupt()
            self._turn_task.cancel()
            await asyncio.gather(self._turn_task, return_exceptions=True)

        self._session_store.save(
            self._stored_session,
            self._agent_session.memory,
            interrupted=False,
        )
        self._stored_session = self._session_store.create_new(model=self._model)
        self._agent_session = new_session(self._agent)

    async def _serve_loop(self) -> None:
        """Receive requests from the transport and dispatch them."""
        while True:
            req = await self._transport.receive_request()
            if req is None:
                return
            await self._dispatch(req)

    async def _dispatch(self, req: Request) -> None:
        """Route one incoming `Request`."""
        if not self._welcomed and not isinstance(req, HelloRequest):
            async with RequestScope(req.id, self._transport.send_event) as scope:
                await scope.error(
                    "protocol",
                    "Handshake required: send a `hello` request first.",
                )
            return

        if isinstance(req, TurnRequest):
            if self._turn_task is not None and not self._turn_task.done():
                async with RequestScope(req.id, self._transport.send_event) as scope:
                    await scope.error(
                        "busy",
                        "Aldwin is still working on your last request. "
                        "Press Esc to stop it first.",
                    )
                return

            turn_scope = RequestScope(req.id, self._transport.send_event)
            self._turn_task = asyncio.create_task(self._run_turn(turn_scope, req))
            return

        async with RequestScope(req.id, self._transport.send_event) as scope:
            if isinstance(req, HelloRequest):
                await self._handle_hello(scope, req)
            elif isinstance(req, StopRequest):
                await self._handle_stop(scope)
            elif isinstance(req, ApproveRequest):
                await self._handle_approve(scope, req)
            elif isinstance(req, HistoryRequest):
                await self._handle_history(scope, req)
            elif isinstance(req, SetModelRequest):
                await self._handle_set_model(scope, req)

    async def _handle_hello(self, scope: RequestScope, req: HelloRequest) -> None:
        if req.v_min <= PROTOCOL_VERSION <= req.v_max:
            self._welcomed = True
            await scope.done("ok")
        else:
            await scope.error(
                "version_mismatch",
                f"Protocol version mismatch: client supports {req.v_min}..{req.v_max}, "
                f"host is v{PROTOCOL_VERSION}.",
            )

    async def _handle_stop(self, scope: RequestScope) -> None:
        self._approvals.cancel_all()
        if self._turn_task is not None and not self._turn_task.done():
            self._agent_session.interrupt()
            self._turn_task.cancel()
            await asyncio.gather(self._turn_task, return_exceptions=True)
        await scope.done("ok")

    async def _handle_approve(self, scope: RequestScope, req: ApproveRequest) -> None:
        if self._approvals.resolve(req.approval_id, req.answer):
            await scope.done("ok")
        else:
            await scope.error(
                "unknown_approval",
                f"Approval {req.approval_id!r} is unknown or has already finished.",
            )

    async def _handle_history(self, scope: RequestScope, req: HistoryRequest) -> None:
        messages = self._session_store.render_history(
            self._agent_session.memory,
            limit=req.limit,
        )
        await scope.emit(
            HistoryEvent(
                re=req.id,
                messages=messages,
                interrupted=self._stored_session.meta.interrupted,
            )
        )
        await scope.done("ok")

    def _is_model_allowed(self, name: str) -> bool:
        cleaned = name.strip()
        if not cleaned:
            return False
        if self._available_models is None:
            return True
        allowed = (
            self._available_models()
            if callable(self._available_models)
            else self._available_models
        )
        return cleaned in allowed

    async def _handle_set_model(
        self,
        scope: RequestScope,
        req: SetModelRequest,
    ) -> None:
        if self._turn_task is not None and not self._turn_task.done():
            await scope.error(
                "busy",
                "Aldwin is still working on your last request. "
                "Press Esc to stop it before switching models.",
            )
            return

        cleaned = req.name.strip()
        if not self._is_model_allowed(cleaned):
            await scope.error(
                "unknown_model",
                f"Model {cleaned!r} is not available. "
                "Run `/model` to pick a supported model.",
            )
            return

        self._model = cleaned
        self._stored_session.meta.model = cleaned
        if self._persist_model:
            update_config_model(cleaned)

        self._agent = self._build_and_attach_agent(self._model)
        self._agent_session = switch_session_model(self._agent_session, self._agent)
        self._session_store.save(
            self._stored_session,
            self._agent_session.memory,
            interrupted=self._stored_session.meta.interrupted,
        )
        await scope.done("ok")

    async def _run_turn(self, scope: RequestScope, req: TurnRequest) -> None:
        """Execute a turn and map Agentling events to protocol events."""
        async with scope:
            self._active_turn_id = req.id
            self._active_turn_scope = scope
            self._agent_session._interrupt.clear()
            step_text_chunks: list[str] = []
            tool_start_times: dict[str, float] = {}
            final_status: str = "completed"
            final_usage: Usage | None = None

            try:
                async for ev in self._agent_session.run(
                    req.text,
                    stream=True,
                    reset=False,
                ):
                    if isinstance(ev, TextDelta):
                        if not step_text_chunks:
                            self._session_store.save(
                                self._stored_session,
                                self._agent_session.memory,
                                interrupted=True,
                            )
                        step_text_chunks.append(ev.text)
                        await scope.emit(TextEvent(re=req.id, delta=ev.text))
                    elif isinstance(ev, AgentlingToolCallEvent):
                        step_text_chunks.clear()
                        call = ev.tool_call
                        tool_start_times[call.id] = time.monotonic()
                        self._session_store.save(
                            self._stored_session,
                            self._agent_session.memory,
                            interrupted=True,
                        )
                        await scope.emit(
                            ToolCallEvent(
                                re=req.id,
                                call_id=call.id,
                                name=call.name,
                                args_preview=format_args_preview(call.arguments),
                            )
                        )
                    elif isinstance(ev, AgentlingToolResultEvent):
                        res = ev.result
                        started = tool_start_times.pop(res.tool_call_id, None)
                        explicit_ms = getattr(res.content, "duration_ms", None)
                        if isinstance(explicit_ms, int):
                            duration_ms = max(0, explicit_ms)
                        elif started is not None:
                            effective_start = max(started, self._last_approval_end_time)
                            duration_ms = max(
                                0, int((time.monotonic() - effective_start) * 1000)
                            )
                        else:
                            duration_ms = 0
                        ok, summary = summarize_tool_result(
                            res.name,
                            res.content,
                            is_error=res.is_error,
                        )
                        await scope.emit(
                            ToolResultEvent(
                                re=req.id,
                                call_id=res.tool_call_id,
                                ok=ok,
                                summary=summary,
                                duration_ms=duration_ms,
                            )
                        )
                    elif isinstance(ev, StepEvent):
                        step_text_chunks.clear()
                        self._session_store.save(
                            self._stored_session,
                            self._agent_session.memory,
                            interrupted=True,
                        )
                    elif isinstance(ev, FinalEvent):
                        final_status = ev.status
                        final_usage = ev.usage

                if final_status == "interrupted":
                    self._flush_partial_text(step_text_chunks)
                    self._session_store.save(
                        self._stored_session,
                        self._agent_session.memory,
                        interrupted=False,
                    )
                    await scope.done("stopped", usage=_to_token_usage(final_usage))
                    return

                reason: DoneReason = (
                    "max_steps" if final_status == "max_steps" else "answered"
                )
                self._session_store.save(
                    self._stored_session,
                    self._agent_session.memory,
                    interrupted=False,
                )
                await scope.done(reason, usage=_to_token_usage(final_usage))

            except asyncio.CancelledError:
                self._approvals.cancel_all()
                self._agent_session._interrupt.clear()
                self._flush_partial_text(step_text_chunks)
                self._session_store.save(
                    self._stored_session,
                    self._agent_session.memory,
                    interrupted=False,
                )
                if not scope.terminated:
                    await scope.done("stopped")
                raise
            except Exception as exc:
                self._approvals.cancel_all()
                self._agent_session._interrupt.clear()
                logger.exception("Turn %s failed", req.id)
                self._session_store.save(
                    self._stored_session,
                    self._agent_session.memory,
                    interrupted=True,
                )
                code, msg = classify_turn_error(exc)
                if not scope.terminated:
                    await scope.error(code, msg)
            finally:
                self._active_turn_id = None
                self._active_turn_scope = None

    def _flush_partial_text(self, chunks: list[str]) -> None:
        """Append partial streamed text as a `FinalStep` when stopped mid-stream."""
        partial = "".join(chunks)
        if not partial:
            return
        steps = self._agent_session.memory.steps
        if not steps or not isinstance(steps[-1], FinalStep):
            self._agent_session.memory.add(FinalStep(answer=partial))
        chunks.clear()
