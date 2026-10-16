"""Client API that Python doors use to communicate with the Aldwin host."""

from __future__ import annotations

from aldwin.host.in_process import (
    BootstrapResult,
    ClientBootstrapError,
    PlatformNotSupportedError,
    bootstrap_host,
)


def bootstrap(*, plain: bool = False, debug: bool = False) -> BootstrapResult:
    """Initialise the Aldwin host and return startup metadata for the door."""
    return bootstrap_host(plain=plain, debug=debug)


__all__ = [
    "BootstrapResult",
    "ClientBootstrapError",
    "PlatformNotSupportedError",
    "bootstrap",
]
