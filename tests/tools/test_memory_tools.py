"""Tool/policy tests for remember, recall, and forget."""

from __future__ import annotations

from pathlib import Path

import pytest
from agentling.tools import ToolCallError

from aldwin.core.home import FILE_MODE, atomic_write, ensure_home
from aldwin.core.memory import remember_note
from aldwin.core.memory_index import default_memory_index
from aldwin.core.policy import (
    REGISTRY,
    ApprovalAnswer,
    ApprovalRequest,
    PolicySession,
    looks_like_secret,
    wrap_tools_with_policy,
)
from aldwin.core.tools.memory_tools import (
    RecallTool,
    build_memory_tools,
    running_summary,
)
from aldwin.core.workspace import Workspace


@pytest.fixture
def workspace(tmp_path: Path) -> Workspace:
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace(root)


def _session(home: Path, workspace: Workspace) -> PolicySession:
    return PolicySession(workspace=workspace, home=home)


async def _ask_yes(_req: ApprovalRequest) -> ApprovalAnswer:
    return ApprovalAnswer(answer="yes", source="user")


async def _ask_no(_req: ApprovalRequest) -> ApprovalAnswer:
    return ApprovalAnswer(answer="no", source="user")


def _wrap(
    home: Path,
    workspace: Workspace,
    ask=_ask_yes,
):
    tools = list(build_memory_tools(home=home))
    return {
        t.name: t
        for t in wrap_tools_with_policy(
            tools,
            session=_session(home, workspace),
            ask=ask,
        )
    }


def test_looks_like_secret_and_luhn() -> None:
    assert looks_like_secret("my key is sk-proj-abc12345678901234567890")
    assert looks_like_secret("card 4111 1111 1111 1111")
    assert not looks_like_secret("Lives at 4 Mill Lane")


def test_recall_tool_attrs() -> None:
    tool = RecallTool()
    assert tool.parallel_safe is True
    assert tool.timeout == 10.0
    assert tool.max_output_chars == 8_000
    assert REGISTRY["recall"].tier == "auto"
    assert REGISTRY["recall"].parallel_safe is True


def test_running_summary_recall() -> None:
    assert running_summary("recall", {"query": "address"}) == 'Recall "address"'
    assert running_summary("forget", {"query": "gym"}) == "Forget"
    assert running_summary("list_dir", {}) == ""


async def test_remember_auto_and_secret_refusal(
    aldwin_home: Path,
    workspace: Workspace,
) -> None:
    ensure_home(aldwin_home)
    tools = _wrap(aldwin_home, workspace)
    out = await tools["remember"].call({"text": "Likes tea"})
    assert "saved" in str(out).lower() or str(out) == "saved"
    with pytest.raises(ToolCallError, match="password, key or card"):
        await tools["remember"].call(
            {"text": "my OpenAI key is sk-proj-abc12345678901234567890"}
        )
    with pytest.raises(ToolCallError, match="password, key or card"):
        await tools["remember"].call({"text": "card 4111 1111 1111 1111"})


async def test_remember_too_long(aldwin_home: Path, workspace: Workspace) -> None:
    tools = _wrap(aldwin_home, workspace)
    with pytest.raises(ToolCallError, match="note too long"):
        await tools["remember"].call({"text": "a" * 501})


async def test_replace_happy_and_missing(
    aldwin_home: Path,
    workspace: Workspace,
) -> None:
    ensure_home(aldwin_home)
    remember_note(aldwin_home, "Lives at 12 Elm Road")
    idx = default_memory_index(aldwin_home)
    idx.sync()
    notes, _, _ = idx.stats()
    assert notes >= 1
    # Find the id from the notes file.
    note_file = next((aldwin_home / "memory" / "notes").glob("*.md"))
    text = note_file.read_text(encoding="utf-8")
    note_id = text.split("id=")[1].split()[0]

    tools = _wrap(aldwin_home, workspace, ask=_ask_yes)
    out = await tools["remember"].call(
        {"text": "Lives at 4 Mill Lane", "replace": note_id}
    )
    assert f"updated {note_id}" in str(out)
    assert "4 Mill Lane" in note_file.read_text(encoding="utf-8")
    assert "12 Elm Road" not in note_file.read_text(encoding="utf-8")

    with pytest.raises(ToolCallError, match="no note with id n-0000"):
        await tools["remember"].call({"text": "x", "replace": "n-0000"})


async def test_replace_deny(aldwin_home: Path, workspace: Workspace) -> None:
    ensure_home(aldwin_home)
    remember_note(aldwin_home, "Member of PureGym")
    note_file = next((aldwin_home / "memory" / "notes").glob("*.md"))
    note_id = note_file.read_text(encoding="utf-8").split("id=")[1].split()[0]
    before = note_file.read_text(encoding="utf-8")
    tools = _wrap(aldwin_home, workspace, ask=_ask_no)
    with pytest.raises(ToolCallError, match="Denied by the user"):
        await tools["remember"].call({"text": "gone", "replace": note_id})
    assert note_file.read_text(encoding="utf-8") == before


async def test_forget_one_yes_and_three_matches(
    aldwin_home: Path,
    workspace: Workspace,
) -> None:
    ensure_home(aldwin_home)
    day = "2026-09-14"
    notes = aldwin_home / "memory" / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    atomic_write(
        notes / f"{day}.md",
        f"# Notes {day}\n<!-- aldwin-memory v1 -->\n"
        f"- Member of PureGym Chelmsford <!-- id=m_gym000000001 at=t src=s -->\n",
        mode=FILE_MODE,
    )
    tools = _wrap(aldwin_home, workspace, ask=_ask_yes)
    out = await tools["forget"].call({"query": "PureGym"})
    assert "forgotten m_gym000000001" in str(out)
    assert not (notes / f"{day}.md").exists()

    atomic_write(
        notes / f"{day}.md",
        f"# Notes {day}\n<!-- aldwin-memory v1 -->\n"
        "- gym one <!-- id=m_gym000000001 at=t src=s -->\n"
        "- gym two <!-- id=m_gym000000002 at=t src=s -->\n"
        "- gym three <!-- id=m_gym000000003 at=t src=s -->\n",
        mode=FILE_MODE,
    )
    with pytest.raises(ToolCallError) as multi:
        await tools["forget"].call({"query": "gym"})
    multi_text = str(multi.value)
    assert "several notes match; call forget with the id of the one to remove" in (
        multi_text
    )
    assert "m_gym000000001" in multi_text
    assert "m_gym000000002" in multi_text
    assert "m_gym000000003" in multi_text
    assert (notes / f"{day}.md").exists()


async def test_forget_deny(aldwin_home: Path, workspace: Workspace) -> None:
    ensure_home(aldwin_home)
    remember_note(aldwin_home, "old gym membership")
    note_file = next((aldwin_home / "memory" / "notes").glob("*.md"))
    before = note_file.read_bytes()
    tools = _wrap(aldwin_home, workspace, ask=_ask_no)
    with pytest.raises(ToolCallError, match="Denied by the user"):
        await tools["forget"].call({"query": "gym"})
    assert note_file.read_bytes() == before


async def test_recall_summary(aldwin_home: Path, workspace: Workspace) -> None:
    ensure_home(aldwin_home)
    remember_note(aldwin_home, "running shoes size 10")
    remember_note(aldwin_home, "ran 5k today")
    tools = _wrap(aldwin_home, workspace)
    out = await tools["recall"].call({"query": "run", "scope": "notes"})
    assert "running" in str(out) or "ran" in str(out)
    assert "Recall" in out.summary


async def test_scope_replace_does_not_touch_profile(
    aldwin_home: Path,
    workspace: Workspace,
) -> None:
    ensure_home(aldwin_home)
    profile = aldwin_home / "memory" / "profile.md"
    profile_before = profile.read_bytes()
    remember_note(aldwin_home, "fact one")
    note_id = (
        next((aldwin_home / "memory" / "notes").glob("*.md"))
        .read_text(encoding="utf-8")
        .split("id=")[1]
        .split()[0]
    )
    tools = _wrap(aldwin_home, workspace, ask=_ask_yes)
    await tools["remember"].call({"text": "fact two", "replace": note_id})
    await tools["forget"].call({"query": note_id})
    assert profile.read_bytes() == profile_before
