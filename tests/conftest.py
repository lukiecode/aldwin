"""Shared pytest fixtures for Aldwin."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes.model import FakeModel


@pytest.fixture
def aldwin_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point ALDWIN_HOME at an isolated temporary directory for the test."""
    home = tmp_path / "aldwin-home"
    monkeypatch.setenv("ALDWIN_HOME", str(home))
    return home


@pytest.fixture
def fake_model() -> FakeModel:
    """Return a fresh, network-free `FakeModel` instance."""
    return FakeModel()
