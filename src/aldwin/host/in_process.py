"""In-process host bootstrap, setup/inspect bridge, and queue transport for Aldwin."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from pydantic import SecretStr

from aldwin.core.home import ensure_home, format_home_path, home_dir
from aldwin.core.logging import setup_logging
from aldwin.core.memory import (
    MemorySummary,
    NoteEntry,
    memory_summary,
    pop_profile_truncation_warning,
)
from aldwin.core.persona import PersonaSummary, persona_summary, persona_text
from aldwin.core.settings import (
    ConfigError,
    KeySource,
    SecretsConfig,
    load_config,
    mask_secret,
    resolve_key,
    save_secrets,
    update_config_model,
)
from aldwin.core.tools import ToolInfo, list_builtin_tools

if TYPE_CHECKING:
    from aldwin.client.client import AldwinClient
    from aldwin.protocol import Event, Request

logger = logging.getLogger("aldwin.host")

NewConversationHandler = Callable[[], Awaitable[None]]
KeyCheckResult = Literal["ok", "invalid", "no_access", "network_error"]


@dataclass(frozen=True, slots=True)
class OnboardingStatus:
    """Current API key and model configuration state for onboarding and `setup`."""

    has_openai_key: bool
    openai_source: KeySource
    masked_openai_key: str
    has_tavily_key: bool
    tavily_source: KeySource
    masked_tavily_key: str
    model: str


@dataclass(frozen=True, slots=True)
class SkillSummary:
    """One valid skill entry returned for `/skills` (`FR-5`)."""

    name: str
    description: str


@dataclass(frozen=True, slots=True)
class InvalidSkillSummary:
    """One invalid skill folder flagged for `/skills` (`FR-5`)."""

    name: str
    display_path: str
    reason: str


@dataclass(frozen=True, slots=True)
class SkillsReport:
    """Installed valid skills and invalid skill folders for `/skills` (`FR-5`)."""

    valid: tuple[SkillSummary, ...]
    invalid: tuple[InvalidSkillSummary, ...]


def get_onboarding_status(home: Path | None = None) -> OnboardingStatus:
    """Inspect whether usable OpenAI/Tavily keys exist and return masked metadata."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    openai_res = resolve_key("openai", root)
    tavily_res = resolve_key("tavily", root)
    return OnboardingStatus(
        has_openai_key=bool(openai_res.key),
        openai_source=openai_res.source,
        masked_openai_key=mask_secret(openai_res.key),
        has_tavily_key=bool(tavily_res.key),
        tavily_source=tavily_res.source,
        masked_tavily_key=mask_secret(tavily_res.key),
        model=config.model,
    )


async def check_openai_key(api_key: str) -> KeyCheckResult:
    """Validate an OpenAI API key via `aldwin.core.models.check_key` (`FR-2`)."""
    from aldwin.core.models import KeyStatus, check_key

    status = await check_key(api_key)
    if status == KeyStatus.OK:
        return "ok"
    if status == KeyStatus.NO_ACCESS:
        return "no_access"
    if status == KeyStatus.NETWORK_ERROR:
        return "network_error"
    return "invalid"


async def list_available_models(
    api_key: str | None = None,
    home: Path | None = None,
) -> list[str]:
    """Return available OpenAI chat models for the active or supplied API key."""
    from aldwin.core.models import list_chat_models

    root = (home if home is not None else home_dir()).expanduser().resolve()
    config = load_config(root)
    effective_key = (api_key or "").strip()
    if not effective_key:
        resolved = resolve_key("openai", root)
        effective_key = (resolved.key or "").strip()

    if not effective_key:
        return [config.model]

    try:
        models = await list_chat_models(effective_key)
    except Exception as exc:
        logger.warning("Failed to list OpenAI chat models: %s", exc)
        return [config.model]

    if not models:
        return [config.model]
    if config.model not in models:
        return [config.model, *models]
    return models


def save_setup(
    *,
    openai_api_key: str | None = None,
    tavily_api_key: str | None = None,
    model: str | None = None,
    home: Path | None = None,
) -> None:
    """Persist updated API keys to `secrets.yaml` and model to `config.yaml`."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    ensure_home(root)

    openai_clean = (openai_api_key or "").strip()
    tavily_clean = (tavily_api_key or "").strip()
    if openai_clean or tavily_clean:
        secrets = SecretsConfig(
            openai_api_key=SecretStr(openai_clean) if openai_clean else None,
            tavily_api_key=SecretStr(tavily_clean) if tavily_clean else None,
        )
        save_secrets(secrets, root)

    model_clean = (model or "").strip()
    if model_clean:
        update_config_model(model_clean, root)


def get_skills_report(home: Path | None = None) -> SkillsReport:
    """Load and validate skills in `~/.aldwin/skills/` for `/skills` (`FR-5`)."""
    from aldwin.core.agent import SKILLS_SUBDIR, load_skills

    root = (home if home is not None else home_dir()).expanduser().resolve()
    result = load_skills(root / SKILLS_SUBDIR)
    valid = tuple(
        SkillSummary(name=s.name, description=s.description) for s in result.valid
    )
    invalid = tuple(
        InvalidSkillSummary(
            name=inv.name,
            display_path=format_home_path(inv.path),
            reason=inv.reason,
        )
        for inv in result.invalid
    )
    return SkillsReport(valid=valid, invalid=invalid)


def purge_home_data(home: Path | None = None) -> Path:
    """Delete the Aldwin home folder (`~/.aldwin` or `ALDWIN_HOME`) for `--purge`."""
    root = (home if home is not None else home_dir()).expanduser().resolve()
    if root.exists():
        shutil.rmtree(root)
    return root


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


def get_builtin_tools() -> list[ToolInfo]:
    """Return one-line descriptions of the built-in tools for `/tools` (`FR-7`)."""
    return list_builtin_tools()


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


@asynccontextmanager
async def open_in_process_client(
    *,
    initial_model: str | None = None,
    home: Path | None = None,
) -> AsyncIterator[AldwinClient]:
    """Start an in-process `AldwinHost` and yield a connected `AldwinClient`."""
    from aldwin.client.client import AldwinClient
    from aldwin.host.runner import AldwinHost
    from aldwin.host.sessions import SessionStore

    root = (home if home is not None else home_dir()).expanduser().resolve()
    transport = InProcessTransport()
    store = SessionStore(sessions_dir=root / "sessions")
    host = AldwinHost(
        transport=transport,
        session_store=store,
        initial_model=initial_model,
    )
    await host.start()
    client = AldwinClient(
        transport.connect(),
        model_getter=lambda: host.model,
        session_id_getter=lambda: host.stored_session.meta.id,
        updated_at_getter=lambda: host.stored_session.meta.updated_at,
        home=root,
    )
    try:
        await client.connect()
        yield client
    finally:
        await client.close()
        await host.stop_host()


__all__ = [
    "BootstrapResult",
    "ClientBootstrapError",
    "ClientTransport",
    "InProcessTransport",
    "InvalidSkillSummary",
    "KeyCheckResult",
    "MemorySummary",
    "NewConversationHandler",
    "NoteEntry",
    "OnboardingStatus",
    "PersonaSummary",
    "PlatformNotSupportedError",
    "SkillSummary",
    "SkillsReport",
    "ToolInfo",
    "bootstrap_host",
    "check_openai_key",
    "get_builtin_tools",
    "get_memory_summary",
    "get_onboarding_status",
    "get_persona_summary",
    "get_persona_text",
    "get_skills_report",
    "list_available_models",
    "mask_secret",
    "open_in_process_client",
    "pop_profile_truncation_warning",
    "purge_home_data",
    "save_setup",
]
