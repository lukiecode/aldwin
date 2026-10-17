"""Architecture boundary guard tests (TC-13)."""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ALDWIN = REPO_ROOT / "src" / "aldwin"


def test_cli_never_imports_core() -> None:
    """A door (`aldwin.cli`) must never import `aldwin.core` or Agentling."""
    cli_files = list((SRC_ALDWIN / "cli").rglob("*.py"))
    assert cli_files, "Expected at least one Python file in src/aldwin/cli"
    for path in cli_files:
        text = path.read_text(encoding="utf-8")
        assert "aldwin.core" not in text, f"{path} must not import aldwin.core"
        assert "agentling" not in text, f"{path} must not import agentling"


def test_client_never_imports_core_or_agentling() -> None:
    """The client (`aldwin.client`) must never import `aldwin.core` or Agentling."""
    client_files = list((SRC_ALDWIN / "client").rglob("*.py"))
    assert client_files, "Expected at least one Python file in src/aldwin/client"
    for path in client_files:
        text = path.read_text(encoding="utf-8")
        assert "aldwin.core" not in text, f"{path} must not import aldwin.core"
        assert "agentling" not in text, f"{path} must not import agentling"


def test_protocol_never_imports_core_or_agentling() -> None:
    """The protocol (`aldwin.protocol`) only depends on stdlib and Pydantic."""
    protocol_files = list((SRC_ALDWIN / "protocol").rglob("*.py"))
    assert protocol_files, "Expected at least one Python file in src/aldwin/protocol"
    for path in protocol_files:
        text = path.read_text(encoding="utf-8")
        assert "aldwin.core" not in text, f"{path} must not import aldwin.core"
        assert "agentling" not in text, f"{path} must not import agentling"
