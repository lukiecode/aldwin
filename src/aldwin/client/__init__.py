"""Aldwin notepad (`client`): the API every Python door uses to talk to the host."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aldwin.host.in_process import (
    BootstrapResult,
    ClientBootstrapError,
    PlatformNotSupportedError,
    bootstrap_host,
)

if TYPE_CHECKING:
    from aldwin.client.client import AldwinClient, ProtocolError


def bootstrap(*, plain: bool = False, debug: bool = False) -> BootstrapResult:
    """Initialise the Aldwin host and return startup metadata for the door."""
    return bootstrap_host(plain=plain, debug=debug)


def __getattr__(name: str) -> Any:
    """Lazily import `AldwinClient` and `ProtocolError` when accessed."""
    if name in ("AldwinClient", "ProtocolError"):
        from aldwin.client import client

        return getattr(client, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AldwinClient",
    "BootstrapResult",
    "ClientBootstrapError",
    "PlatformNotSupportedError",
    "ProtocolError",
    "bootstrap",
]
