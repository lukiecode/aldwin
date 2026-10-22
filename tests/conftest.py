"""Shared pytest fixtures and helpers for Aldwin (`FR-2` to `FR-5`)."""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from agentling import AgentSession
from fakes.host import FakeHost
from fakes.model import FakeModel

from aldwin.client import AldwinClient
from aldwin.core.agent import build_agent, new_session
from aldwin.core.home import ensure_home
from aldwin.core.settings import AldwinConfig, SecretsConfig
from aldwin.host import AldwinHost, AskFn, InProcessTransport
from aldwin.protocol import ApprovalAnswer, ApprovalEvent, Event

_ORIG_SOCKET_CONNECT = socket.socket.connect
_ORIG_SOCKET_CONNECT_EX = socket.socket.connect_ex


async def _allow_ask(_summary: str, _detail: str = "") -> bool:
    return True


async def collect(aiter: AsyncIterator[Event]) -> list[Event]:
    """Drain an async iterator of protocol `Event`s into a list (`FR-4`)."""
    events: list[Event] = []
    async for ev in aiter:
        events.append(ev)
    return events


async def auto_approve(
    client: AldwinClient,
    aiter: AsyncIterator[Event],
    answer: ApprovalAnswer = "yes",
) -> list[Event]:
    """Consume a turn stream and automatically answer every `ApprovalEvent` (`FR-4`)."""
    events: list[Event] = []
    async for ev in aiter:
        events.append(ev)
        if isinstance(ev, ApprovalEvent):
            await client.approve(ev.approval_id, answer)
    return events


_NET_ERR = "Network access is disabled in default tests; mark with @pytest.mark.live"


@pytest.fixture(autouse=True)
def aldwin_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Path:
    """Redirect `ALDWIN_HOME` and `HOME` to `tmp_path` and run `ensure_home()`."""
    user_home = tmp_path / "user"
    user_home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(user_home))
    # Some sandboxes export TERM=dumb, NO_COLOR, and FORCE_COLOR=0.
    # Rich then ignores an explicit width, drops styles, and treats the
    # console as non-interactive. Tests that need plain mode set NO_COLOR
    # themselves.
    monkeypatch.setenv("TERM", "xterm-256color")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)

    home = tmp_path / "home"
    monkeypatch.setenv("ALDWIN_HOME", str(home))

    if "live" not in request.keywords:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    if "uninitialized_home" not in request.keywords:
        ensure_home(home)

    return home


@pytest.fixture(autouse=True)
def no_network(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Block outbound `AF_INET`/`AF_INET6` sockets unless marked `live` (`TC-01`)."""
    if "live" in request.keywords:
        return

    def _blocked_connect(self: socket.socket, address: Any) -> Any:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError(_NET_ERR)
        return _ORIG_SOCKET_CONNECT(self, address)

    def _blocked_connect_ex(self: socket.socket, address: Any) -> int:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise RuntimeError(_NET_ERR)
        return _ORIG_SOCKET_CONNECT_EX(self, address)

    def _blocked_create_connection(*_args: Any, **_kwargs: Any) -> socket.socket:
        raise RuntimeError(_NET_ERR)

    monkeypatch.setattr(socket.socket, "connect", _blocked_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", _blocked_connect_ex)
    monkeypatch.setattr(socket, "create_connection", _blocked_create_connection)


@pytest.fixture
def fake_model() -> FakeModel:
    """Return a fresh, network-free `FakeModel` instance (`FR-3`)."""
    return FakeModel()


@pytest.fixture
def agent_session(fake_model: FakeModel, aldwin_home: Path) -> AgentSession:
    """Build a real `aldwin.core` `AgentSession` wired to `fake_model` (`FR-4`)."""
    agent = build_agent(
        AldwinConfig(),
        SecretsConfig(),
        _allow_ask,
        home=aldwin_home,
        model=fake_model,
    )
    return new_session(agent)


@pytest.fixture
async def host_client(
    fake_model: FakeModel,
    aldwin_home: Path,
) -> AsyncIterator[AldwinClient]:
    """Start an in-process `AldwinHost` on `fake_model` and yield a connected client."""

    def builder(model_name: str, ask: AskFn) -> Any:
        return build_agent(
            AldwinConfig(model=model_name),
            SecretsConfig(),
            ask,
            home=aldwin_home,
            model=fake_model,
        )

    transport = InProcessTransport()
    host = AldwinHost(transport, agent_builder=builder)
    await host.start()
    client = AldwinClient(transport.connect())
    await client.connect()
    try:
        yield client
    finally:
        await client.close()
        await host.stop_host()


@pytest.fixture
def fake_host() -> FakeHost:
    """Return a scriptable `FakeHost` test double for CLI tests (`FR-5`)."""
    return FakeHost()
