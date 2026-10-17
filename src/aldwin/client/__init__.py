"""Aldwin notepad (`client`): the API every Python door uses to talk to the host."""

from aldwin.client.client import (
    BootstrapResult,
    ClientBootstrapError,
    PlatformNotSupportedError,
    bootstrap,
)

__all__ = [
    "BootstrapResult",
    "ClientBootstrapError",
    "PlatformNotSupportedError",
    "bootstrap",
]
