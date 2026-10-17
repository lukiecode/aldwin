"""Aldwin notepad (`client`): the API every Python door uses to talk to the host."""

from aldwin.client.client import (
    AldwinClient,
    BootstrapResult,
    ClientBootstrapError,
    PlatformNotSupportedError,
    ProtocolError,
    bootstrap,
)

__all__ = [
    "AldwinClient",
    "BootstrapResult",
    "ClientBootstrapError",
    "PlatformNotSupportedError",
    "ProtocolError",
    "bootstrap",
]
