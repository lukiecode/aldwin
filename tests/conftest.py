"""Shared pytest fixtures for Aldwin."""

from pathlib import Path

import pytest


@pytest.fixture
def aldwin_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ALDWIN_HOME at an isolated temporary directory for the test."""
    home = tmp_path / "aldwin-home"
    monkeypatch.setenv("ALDWIN_HOME", str(home))
    return home
