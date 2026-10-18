"""Client API that Python doors use to communicate with the Aldwin host."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from typing import Final

from aldwin.host.in_process import (
    BootstrapResult,
    ClientBootstrapError,
    ClientTransport,
    MemorySummary,
    NoteEntry,
    PersonaSummary,
    PlatformNotSupportedError,
    bootstrap_host,
    get_memory_summary,
    get_persona_summary,
    get_persona_text,
    pop_profile_truncation_warning,
)
from aldwin.protocol import (
    PROTOCOL_VERSION,
    ApprovalAnswer,
    ApproveRequest,
    DoneEvent,
    ErrorCode,
    ErrorEvent,
    Event,
    HelloRequest,
    HistoryEvent,
    HistoryRequest,
    Request,
    SetModelRequest,
    StopRequest,
    TurnRequest,
)


def bootstrap(*, plain: bool = False, debug: bool = False) -> BootstrapResult:
    """Initialise the Aldwin host and return startup metadata for the door."""
    return bootstrap_host(plain=plain, debug=debug)


class ProtocolError(Exception):
    """Raised when the host rejects a handshake or closes before a terminal event."""

    def __init__(self, message: str, *, code: ErrorCode | None = None) -> None:
        super().__init__(message)
        self.code: Final[ErrorCode | None] = code


class AldwinClient:
    """Async client that every Python door uses to talk to the Aldwin host (`FR-16`)."""

    def __init__(self, transport: ClientTransport) -> None:
        self._transport = transport
        self._queues: dict[str, asyncio.Queue[Event | None]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    def _ensure_reader(self) -> None:
        if self._reader_task is None or self._reader_task.done():
            self._reader_task = asyncio.create_task(self._read_events_loop())

    async def _read_events_loop(self) -> None:
        try:
            async for event in self._transport.events():
                if isinstance(event, (DoneEvent, ErrorEvent)):
                    queue = self._queues.pop(event.re, None)
                else:
                    queue = self._queues.get(event.re)
                if queue is not None:
                    await queue.put(event)
        finally:
            for queue in list(self._queues.values()):
                await queue.put(None)

    async def _request_terminal(self, request: Request) -> DoneEvent | ErrorEvent:
        self._ensure_reader()
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError(
                        f"Transport closed before request {request.id!r} completed."
                    )
                if isinstance(event, (DoneEvent, ErrorEvent)):
                    return event
        finally:
            self._queues.pop(request.id, None)

    async def connect(
        self,
        *,
        v_min: int = PROTOCOL_VERSION,
        v_max: int = PROTOCOL_VERSION,
    ) -> DoneEvent:
        """Perform the initial `hello` handshake with the host (`FR-4`)."""
        terminal = await self._request_terminal(HelloRequest(v_min=v_min, v_max=v_max))
        if isinstance(terminal, ErrorEvent):
            raise ProtocolError(terminal.message, code=terminal.code)
        return terminal

    async def turn(self, text: str) -> AsyncGenerator[Event, None]:
        """Send a `turn` request and yield events until `done` or `error` (`FR-7`)."""
        self._ensure_reader()
        request = TurnRequest(text=text)
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    return
                yield event
                if isinstance(event, (DoneEvent, ErrorEvent)):
                    return
        finally:
            self._queues.pop(request.id, None)

    async def stop(self) -> DoneEvent | ErrorEvent:
        """Stop the currently running turn (`FR-8`)."""
        return await self._request_terminal(StopRequest())

    async def approve(
        self,
        approval_id: str,
        answer: ApprovalAnswer,
    ) -> DoneEvent | ErrorEvent:
        """Send a `yes` or `no` answer for a pending approval (`FR-9`)."""
        return await self._request_terminal(
            ApproveRequest(approval_id=approval_id, answer=answer)
        )

    async def history(self, limit: int = 20) -> HistoryEvent:
        """Fetch recent conversation messages and `interrupted` flag (`FR-13`)."""
        self._ensure_reader()
        request = HistoryRequest(limit=limit)
        queue: asyncio.Queue[Event | None] = asyncio.Queue()
        self._queues[request.id] = queue
        history_event: HistoryEvent | None = None
        try:
            await self._transport.send(request)
            while True:
                event = await queue.get()
                if event is None:
                    raise ProtocolError("Transport closed before `history` completed.")
                if isinstance(event, HistoryEvent):
                    history_event = event
                elif isinstance(event, ErrorEvent):
                    raise ProtocolError(event.message, code=event.code)
                elif isinstance(event, DoneEvent):
                    if history_event is None:
                        return HistoryEvent(re=request.id)
                    return history_event
        finally:
            self._queues.pop(request.id, None)

    async def set_model(self, name: str) -> DoneEvent | ErrorEvent:
        """Switch the active model for subsequent turns (`FR-15`)."""
        return await self._request_terminal(SetModelRequest(name=name))

    async def new_conversation(self) -> None:
        """Close the current session file and start a new conversation (`FR-14`)."""
        await self._transport.new_conversation()

    async def close(self) -> None:
        """Close the client and underlying transport."""
        if self._closed:
            return
        self._closed = True
        await self._transport.close()
        if self._reader_task is not None and not self._reader_task.done():
            await asyncio.gather(self._reader_task, return_exceptions=True)


__all__ = [
    "AldwinClient",
    "BootstrapResult",
    "ClientBootstrapError",
    "MemorySummary",
    "NoteEntry",
    "PersonaSummary",
    "PlatformNotSupportedError",
    "ProtocolError",
    "bootstrap",
    "get_memory_summary",
    "get_persona_summary",
    "get_persona_text",
    "pop_profile_truncation_warning",
]
