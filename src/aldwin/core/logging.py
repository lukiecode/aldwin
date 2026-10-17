"""Rotating file logging configuration and secret redaction filter for Aldwin."""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Final

from aldwin.core.home import DIR_MODE, FILE_MODE, ensure_permissions, home_dir
from aldwin.core.settings import mask_secret

LOGGER_NAME: Final[str] = "aldwin"
LOG_REL_PATH: Final[str] = "logs/aldwin.log"
MAX_LOG_BYTES: Final[int] = 1_000_000
BACKUP_COUNT: Final[int] = 3
LOG_FORMAT: Final[str] = "%(asctime)s %(levelname)s %(name)s %(message)s"

_SECRET_TOKEN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:sk-(?:proj-|ant-)?|tvly-(?:dev-|prod-)?)[A-Za-z0-9_-]{8,}\b"
)


def _redact_text(text: str) -> str:
    """Replace any API key tokens in ``text`` with their masked form."""
    return _SECRET_TOKEN_PATTERN.sub(lambda m: mask_secret(m.group(0)), text)


class SecretRedactingFilter(logging.Filter):
    """Logging filter that masks API keys before records reach disk."""

    def filter(self, record: logging.LogRecord) -> bool:
        formatted = record.getMessage()
        redacted = _redact_text(formatted)
        record.msg = redacted
        record.args = ()
        return True


def setup_logging(home: Path | None = None, *, debug: bool = False) -> Path:
    """Configure rotating file logging in ``~/.aldwin/logs/aldwin.log``.

    Creates ``logs/`` (mode ``0700``) and ``aldwin.log`` (mode ``0600``), rotates
    at 1 MB keeping 3 backups, attaches ``SecretRedactingFilter``, and disables
    propagation so log lines never pollute terminal output.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    log_path = root / LOG_REL_PATH
    log_dir = log_path.parent
    log_dir.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    ensure_permissions(log_dir, DIR_MODE)

    aldwin_logger = logging.getLogger(LOGGER_NAME)
    aldwin_logger.setLevel(logging.DEBUG if debug else logging.INFO)
    aldwin_logger.propagate = False

    for existing_handler in list(aldwin_logger.handlers):
        aldwin_logger.removeHandler(existing_handler)
        existing_handler.close()

    handler = RotatingFileHandler(
        log_path,
        maxBytes=MAX_LOG_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    os.chmod(log_path, FILE_MODE)

    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(SecretRedactingFilter())
    aldwin_logger.addHandler(handler)

    return log_path
