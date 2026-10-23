"""Detect how Aldwin was installed so `aldwin uninstall` can act or print."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final, Literal

InstallMethod = Literal["uv", "pipx", "pip", "unknown"]

_UV_MARKERS: Final[tuple[str, ...]] = (
    "/uv/tools/",
    "/uv/tool/",
    "\\uv\\tools\\",
    "\\uv\\tool\\",
    "/uv-tool/",
    "\\uv-tool\\",
)
_PIPX_MARKERS: Final[tuple[str, ...]] = (
    "/pipx/venvs/",
    "\\pipx\\venvs\\",
    "/.local/pipx/",
    "\\.local\\pipx\\",
    "/pipx/bin/",
    "\\pipx\\bin\\",
)


def _path_blob() -> str:
    """Return a lowercased path string used for installer heuristics."""
    parts = [
        sys.prefix,
        getattr(sys, "base_prefix", ""),
        sys.executable,
        str(Path(sys.executable).resolve()),
    ]
    return " ".join(parts).lower()


def detect_install_method(
    *,
    path_blob: str | None = None,
) -> InstallMethod:
    """Return how this Aldwin process was most likely installed.

    Prefers path/`sys.prefix` markers over running subprocesses so tests can
    inject `path_blob` without touching the real environment.
    """
    blob = (path_blob if path_blob is not None else _path_blob()).lower()
    if any(marker in blob for marker in _UV_MARKERS):
        return "uv"
    if any(marker in blob for marker in _PIPX_MARKERS):
        return "pipx"
    if "site-packages" in blob or "venv" in blob or ".venv" in blob:
        return "pip"
    return "unknown"


def uninstall_command(method: InstallMethod) -> str:
    """Return the shell command that removes the aldwin tool for `method`."""
    if method == "pipx":
        return "pipx uninstall aldwin"
    if method == "pip":
        return "pip uninstall aldwin"
    # uv and unknown: prefer the documented uv tool command
    return "uv tool uninstall aldwin"
