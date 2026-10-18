"""In-process host bootstrap and queue-backed transport for Aldwin."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from aldwin.core.home import ensure_home, format_home_path, home_dir
from aldwin.core.logging import setup_logging
from aldwin.core.memory import (
    MemorySummary,
    NoteEntry,
    memory_summary,
    pop_profile_truncation_warning,
)
from aldwin.core.persona import PersonaSummary, persona_summary, persona_text
from aldwin.core.settings import ConfigError, load_config

if TYPE_CHECKING:
    from aldwin.protocol import Event, Request

logger = logging.getLogger("aldwin.host")

NewConversationHandler = Callable[[], Awaitable[None]]


def get_memory_summary(home: Path | None = None) -> MemorySummary:
    """Return profile and notes summary data for `/memory` (`FR-10`)."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    return memory_summary(root, config.limits)


def get_persona_text(home: Path | None = None) -> str:
    """Return the active `persona.md` text for `/persona` (`FR-10`)."""
    return persona_text(home)


def get_persona_summary(home: Path | None = None) -> PersonaSummary:
    """Return path, text, and cap metadata for `/persona` (`FR-10`)."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    return persona_summary(root, cap=config.limits.persona_chars)


class PlatformNotSupportedError(Exception):
    """Raised when Aldwin is launched on an unsupported operating system."""


class ClientBootstrapError(Exception):
    """Raised when the home folder or configuration cannot be initialised."""


@dataclass(frozen=True)
class BootstrapResult:
    """Summary returned to a door after the host initialises the home folder."""

    home: Path
    display_home: str
    created: bool
    model: str
    plain: bool


def bootstrap_host(*, plain: bool = False, debug: bool = False) -> BootstrapResult:
    """Initialise the Aldwin home folder, rotating log, and configuration.

    Raises ``PlatformNotSupportedError`` on Windows and ``ClientBootstrapError``
    when ``config.yaml`` is invalid.
    """
    if sys.platform == "win32":
        raise PlatformNotSupportedError(
            "Aldwin does not support Windows yet. Please use macOS or Linux."
        )

    root = home_dir()
    if root.exists():
        setup_logging(root, debug=debug)
        created = ensure_home(root)
    else:
        created = ensure_home(root)
        setup_logging(root, debug=debug)

    try:
        config = load_config(root)
    except ConfigError as exc:
        logger.error("configuration_error %s", exc)
        raise ClientBootstrapError(str(exc)) from None

    effective_plain = (
        plain or bool(os.environ.get("NO_COLOR", "")) or config.theme == "plain"
    )

    logger.info(
        "aldwin.startup home=%s created=%s model=%s plain=%s",
        root,
        created,
        config.model,
        effective_plain,
    )
    return BootstrapResult(
        home=root,
        display_home=format_home_path(root),
        created=created,
        model=config.model,
        plain=effective_plain,
    )


class ClientTransport(Protocol):
    """Client-side transport handle used by `AldwinClient`."""

    async def send(self, request: Request) -> None:
        """Send a protocol request to the host."""
        ...

    def events(self) -> AsyncIterator[Event]:
        """Yield protocol events from the host until the transport closes."""
        ...

    async def new_conversation(self) -> None:
        """Ask the host to close the current session file and start a new one."""
        ...

    async def close(self) -> None:
        """Close the transport connection."""
        ...


class _InProcessClientEndpoint:
    """Client-side view of an `InProcessTransport`."""

    def __init__(self, transport: InProcessTransport) -> None:
        self._transport = transport

    async def send(self, request: Request) -> None:
        """Validate `request` through JSON and enqueue it for the host."""
        await self._transport.put_request(request)

    async def events(self) -> AsyncIterator[Event]:
        """Yield events emitted by the host until a `None` sentinel arrives."""
        while True:
            event = await self._transport.next_event()
            if event is None:
                return
            yield event

    async def new_conversation(self) -> None:
        """Invoke the host's new-conversation hook."""
        await self._transport.trigger_new_conversation()

    async def close(self) -> None:
        """Close the shared in-process transport."""
        await self._transport.close()


class InProcessTransport:
    """Pair of `asyncio.Queue`s connecting client and host inside one process."""

    def __init__(
        self,
        *,
        on_new_conversation: NewConversationHandler | None = None,
    ) -> None:
        self._inbound: asyncio.Queue[Request | None] = asyncio.Queue()
        self._outbound: asyncio.Queue[Event | None] = asyncio.Queue()
        self._on_new_conversation = on_new_conversation
        self._closed = False

    def set_new_conversation_handler(self, handler: NewConversationHandler) -> None:
        """Register the host callback invoked when a client starts a new chat."""
        self._on_new_conversation = handler

    def connect(self) -> ClientTransport:
        """Return a client-side handle connected to this transport."""
        return _InProcessClientEndpoint(self)

    async def put_request(self, request: Request) -> None:
        """Round-trip `request` through JSON wire validation and enqueue it."""
        if self._closed:
            raise RuntimeError("Transport is closed.")
        from aldwin.protocol.messages import parse_request

        validated = parse_request(request.model_dump_json())
        await self._inbound.put(validated)

    async def receive_request(self) -> Request | None:
        """Await the next request from the client, or `None` when closed."""
        return await self._inbound.get()

    async def send_event(self, event: Event) -> None:
        """Round-trip `event` through JSON wire validation and enqueue it."""
        if self._closed:
            return
        from aldwin.protocol.messages import parse_event

        validated = parse_event(event.model_dump_json())
        await self._outbound.put(validated)

    async def next_event(self) -> Event | None:
        """Await the next event for the client, or `None` when closed."""
        return await self._outbound.get()

    async def trigger_new_conversation(self) -> None:
        """Run the registered `on_new_conversation` callback if present."""
        if self._on_new_conversation is not None:
            await self._on_new_conversation()

    async def close(self) -> None:
        """Signal both queues to stop waiting and mark the transport closed."""
        if self._closed:
            return
        self._closed = True
        await self._inbound.put(None)
        await self._outbound.put(None)


__all__ = [
    "BootstrapResult",
    "ClientBootstrapError",
    "ClientTransport",
    "InProcessTransport",
    "MemorySummary",
    "NewConversationHandler",
    "NoteEntry",
    "PersonaSummary",
    "PlatformNotSupportedError",
    "bootstrap_host",
    "get_memory_summary",
    "get_persona_summary",
    "get_persona_text",
    "pop_profile_truncation_warning",
]
