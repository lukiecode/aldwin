"""Tests for `~/.aldwin/state/skills_recent.json` (ENG-432)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from aldwin.core.skills_recent import (
    MAX_RECENT,
    load_skills_recent,
    record_skills_used,
    skills_recent_path,
)


def test_missing_recent_file_is_empty(aldwin_home: Path) -> None:
    assert load_skills_recent(aldwin_home) == {}


def test_corrupt_recent_rewritten_on_record(aldwin_home: Path) -> None:
    path = skills_recent_path(aldwin_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not-json", encoding="utf-8")
    assert load_skills_recent(aldwin_home) == {}
    record_skills_used(
        aldwin_home,
        ["files"],
        now=datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc),
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data == {"files": "2026-10-23T12:00:00+00:00"}
    assert (path.stat().st_mode & 0o777) == 0o600


def test_recent_keeps_fifty_newest(aldwin_home: Path) -> None:
    for i in range(60):
        record_skills_used(
            aldwin_home,
            [f"skill-{i:02d}"],
            now=datetime(2026, 1, 1, 0, i % 60, tzinfo=timezone.utc),
        )
    data = load_skills_recent(aldwin_home)
    assert len(data) == MAX_RECENT
    assert "skill-59" in data
    assert "skill-00" not in data


def test_privacy_names_and_timestamps_only(aldwin_home: Path) -> None:
    record_skills_used(aldwin_home, ["files", "memory"])
    raw = skills_recent_path(aldwin_home).read_text(encoding="utf-8")
    assert "List first" not in raw
    assert "<picked_skills>" not in raw
    data = json.loads(raw)
    assert set(data) <= {"files", "memory"}
    for value in data.values():
        assert "T" in value  # ISO timestamp
