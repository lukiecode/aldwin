"""Home directory resolution, creation, permission hardening, and atomic file I/O."""

from __future__ import annotations

import logging
import os
import tempfile
import time
from importlib.resources import files
from pathlib import Path
from typing import Final

logger = logging.getLogger("aldwin.core.home")

HOME_ENV_VAR: Final[str] = "ALDWIN_HOME"
DEFAULT_HOME_DIR: Final[str] = "~/.aldwin"
DIR_MODE: Final[int] = 0o700
FILE_MODE: Final[int] = 0o600
TMP_RETENTION_DAYS: Final[int] = 7
SECONDS_PER_DAY: Final[int] = 86_400

SUBDIRS: Final[tuple[str, ...]] = (
    "skills",
    "sessions",
    "memory",
    "memory/notes",
    "logs",
    "tmp",
    "state",
)

STARTER_ASSETS: Final[tuple[tuple[str, str], ...]] = (
    ("config.yaml", "config.yaml"),
    ("persona.md", "persona.md"),
    ("profile.md", "memory/profile.md"),
)

BASE_INSTRUCTIONS_ASSET: Final[str] = "instructions.md"


def home_dir() -> Path:
    """Return the resolved path to Aldwin's home directory.

    Uses ``ALDWIN_HOME`` when set to a non-blank string; otherwise defaults to
    ``~/.aldwin``. Always expands ``~`` and returns an absolute resolved path.
    """
    raw = os.environ.get(HOME_ENV_VAR, "").strip()
    target = raw if raw else DEFAULT_HOME_DIR
    return Path(target).expanduser().resolve()


def format_home_path(path: Path) -> str:
    """Format a path using ``~`` shorthand when it sits inside the user's home."""
    resolved = path.expanduser().resolve()
    user_home = Path.home().resolve()
    try:
        rel = resolved.relative_to(user_home)
    except ValueError:
        return str(resolved)
    if str(rel) == ".":
        return "~"
    return f"~/{rel}"


def ensure_permissions(path: Path, expected_mode: int) -> bool:
    """Ensure ``path`` does not have looser permissions than ``expected_mode``.

    If ``path`` has any permission bits outside ``expected_mode`` (or is missing
    required owner bits), resets permissions to ``expected_mode``, logs a
    warning, and returns ``True``. Returns ``False`` when no change was needed.
    """
    actual_mode = path.stat().st_mode & 0o777
    if actual_mode != expected_mode:
        os.chmod(path, expected_mode)
        logger.warning(
            "Repaired permissions on %s from %04o to %04o",
            path,
            actual_mode,
            expected_mode,
        )
        return True
    return False


def _ensure_dir(path: Path) -> None:
    """Create directory ``path`` with ``0700`` permissions and verify its mode."""
    existed = path.exists()
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)
    if existed:
        ensure_permissions(path, DIR_MODE)
    else:
        os.chmod(path, DIR_MODE)


def atomic_write(path: Path, content: str | bytes, *, mode: int = FILE_MODE) -> None:
    """Atomically write ``content`` to ``path`` and set file permissions to ``mode``.

    Writes to a temporary file in the same directory, flushes and fsyncs to
    disk, sets permissions, and replaces the target with ``os.replace``. If any
    step fails, the temporary file is removed and the original target is left
    untouched.
    """
    data = content.encode("utf-8") if isinstance(content, str) else content
    parent = path.parent
    _ensure_dir(parent)

    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            dir=parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp_file:
            tmp_path = Path(tmp_file.name)
            tmp_file.write(data)
            tmp_file.flush()
            os.fsync(tmp_file.fileno())

        os.chmod(tmp_path, mode)
        os.replace(tmp_path, path)
    except BaseException:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def ensure_home(home: Path | None = None) -> bool:
    """Ensure Aldwin's home directory, subdirectories, and starter assets exist.

    Idempotent and safe to run concurrently. Never overwrites existing user
    files; on upgrade, only missing starter assets are added. Returns ``True``
    if the root home folder was created during this call, or ``False`` if it
    already existed.
    """
    root = (home if home is not None else home_dir()).expanduser().resolve()
    created_root = not root.exists()

    _ensure_dir(root)
    for rel_dir in SUBDIRS:
        _ensure_dir(root / rel_dir)

    assets_root = files("aldwin.core.assets")
    for asset_rel, dest_rel in STARTER_ASSETS:
        dest = root / dest_rel
        _ensure_dir(dest.parent)
        if not dest.exists():
            asset_bytes = assets_root.joinpath(*asset_rel.split("/")).read_bytes()
            atomic_write(dest, asset_bytes, mode=FILE_MODE)

    secrets_path = root / "secrets.yaml"
    if secrets_path.exists():
        ensure_permissions(secrets_path, FILE_MODE)

    from aldwin.core.skills import migrate_001_starter_skill

    migrate_001_starter_skill(root)

    cleanup_old_tmp_files(root / "tmp")
    return created_root


def cleanup_old_tmp_files(
    tmp_dir: Path,
    *,
    max_age_days: int = TMP_RETENTION_DAYS,
    now: float | None = None,
) -> int:
    """Delete files in `tmp_dir` older than `max_age_days`."""
    if not tmp_dir.is_dir():
        return 0
    current_time = time.time() if now is None else now
    cutoff = current_time - (max_age_days * SECONDS_PER_DAY)
    deleted = 0
    try:
        entries = list(tmp_dir.iterdir())
    except OSError:
        return 0

    for entry in entries:
        try:
            if entry.is_file() or entry.is_symlink():
                stat_res = entry.lstat()
                if stat_res.st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
                    deleted += 1
        except OSError:
            continue
    return deleted


def read_base_instructions() -> str:
    """Read the shipped base instructions from package data."""
    return (
        files("aldwin.core.assets")
        .joinpath(BASE_INSTRUCTIONS_ASSET)
        .read_text(encoding="utf-8")
    )
