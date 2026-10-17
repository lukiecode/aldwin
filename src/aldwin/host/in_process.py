"""In-process host bootstrap and transport for Aldwin."""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from pathlib import Path

from aldwin.core.home import ensure_home, format_home_path, home_dir
from aldwin.core.logging import setup_logging
from aldwin.core.settings import ConfigError, load_config

logger = logging.getLogger("aldwin.host")


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
