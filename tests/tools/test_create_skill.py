"""Unit tests for `create_skill`."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from agentling import Skill
from agentling.tools import ToolCallError

from aldwin.core.policy import ApprovalPreview
from aldwin.core.tools.skill_tools import (
    INVALID_DESCRIPTION,
    INVALID_NAME,
    NO_SECRETS,
    NO_TOOLS,
    CreateSkillTool,
    build_skill_file_text,
    validate_create_skill_args,
)


def test_tc06_invalid_skill_name() -> None:
    with pytest.raises(ToolCallError, match=re.escape(INVALID_NAME)):
        validate_create_skill_args("Weekly Report", "x Load when.", "y")


def test_tc07_multiline_description() -> None:
    with pytest.raises(ToolCallError, match=re.escape(INVALID_DESCRIPTION)):
        validate_create_skill_args("weekly-report", "one\ntwo", "body")


def test_tc08_instructions_start_with_frontmatter() -> None:
    with pytest.raises(ToolCallError, match="body only"):
        validate_create_skill_args("weekly-report", "Desc Load when.", "---\nfoo")


def test_whitespace_only_instructions_rejected() -> None:
    with pytest.raises(ToolCallError, match="1-20000 characters"):
        validate_create_skill_args("weekly-report", "Desc Load when.", "   \n\t  ")


def test_tc17_secret_in_instructions() -> None:
    with pytest.raises(ToolCallError, match=re.escape(NO_SECRETS)):
        validate_create_skill_args(
            "weekly-report",
            "Desc Load when.",
            "key sk-proj-abcdefghijklmnopqrstuvwxyz012345",
        )


def test_tc18_tools_line_in_instructions() -> None:
    with pytest.raises(ToolCallError, match=re.escape(NO_TOOLS)):
        validate_create_skill_args(
            "weekly-report",
            "Desc Load when.",
            "1. Do thing\ntools: []\n2. Done",
        )


@pytest.mark.asyncio
async def test_tc09_create_new_skill_write(aldwin_home: Path) -> None:
    flags: dict[str, bool] = {}
    tool = CreateSkillTool(home=aldwin_home, host_flags=flags)
    preview = tool.prepare_approval(
        "weekly-report",
        "Weekly report Load when asked.",
        "1. Gather notes.\n",
    )
    assert isinstance(preview, ApprovalPreview)
    assert preview.summary.startswith("New skill weekly-report")
    out = await tool.forward(
        "weekly-report",
        "Weekly report Load when asked.",
        "1. Gather notes.\n",
    )
    assert str(out) == "saved skill weekly-report (new)"
    assert out.summary.startswith("Skill created · weekly-report")
    assert flags.get("skills_dirty") is True
    path = aldwin_home / "skills" / "weekly-report" / "SKILL.md"
    assert path.is_file()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    skill = Skill.from_path(path.parent)
    assert skill.name == "weekly-report"


@pytest.mark.asyncio
async def test_tc11_replace_default_summary(aldwin_home: Path) -> None:
    tool = CreateSkillTool(home=aldwin_home, host_flags={})
    preview = tool.prepare_approval(
        "terminal",
        "Custom terminal Load before commands.",
        "1. Explain first.\n",
    )
    assert preview.summary.startswith("Replace default skill terminal")
    assert preview.detail is not None
    assert 'This replaces the default "terminal" skill' in preview.detail


@pytest.mark.asyncio
async def test_update_existing_user_skill(aldwin_home: Path) -> None:
    flags: dict[str, bool] = {}
    tool = CreateSkillTool(home=aldwin_home, host_flags=flags)
    await tool.forward(
        "weekly-report",
        "Weekly report Load when asked.",
        "1. First draft.\n",
    )
    preview = tool.prepare_approval(
        "weekly-report",
        "Weekly report Load when asked.",
        "1. Second draft.\n",
    )
    assert preview.summary.startswith("Update skill weekly-report")
    out = await tool.forward(
        "weekly-report",
        "Weekly report Load when asked.",
        "1. Second draft.\n",
    )
    assert str(out) == "saved skill weekly-report (updated)"
    assert out.summary.startswith("Skill updated · weekly-report")
    assert "Second draft" in (
        aldwin_home / "skills" / "weekly-report" / "SKILL.md"
    ).read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_replace_default_skill_write(aldwin_home: Path) -> None:
    flags: dict[str, bool] = {}
    tool = CreateSkillTool(home=aldwin_home, host_flags=flags)
    out = await tool.forward(
        "terminal",
        "Custom terminal Load before commands.",
        "1. Explain first.\n",
    )
    assert str(out) == "saved skill terminal (replaces default)"
    assert out.summary.startswith("Skill updated · terminal")
    assert flags.get("skills_dirty") is True
    assert (aldwin_home / "skills" / "terminal" / "SKILL.md").is_file()


def test_tc12_yaml_roundtrip_special_chars(aldwin_home: Path) -> None:
    desc = 'Use for: "weekly" #reports'
    text = build_skill_file_text("weekly-report", desc, "1. Go.\n")
    folder = aldwin_home / "skills" / "weekly-report"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(text, encoding="utf-8")
    skill = Skill.from_path(folder)
    assert skill.name == "weekly-report"
    assert skill.description == desc


@pytest.mark.asyncio
async def test_create_skill_write_oserror(aldwin_home: Path, monkeypatch) -> None:
    from aldwin.core.tools import skill_tools

    async def _boom(_target: Path, _text: str) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(skill_tools, "_atomic_write_skill", _boom)
    tool = CreateSkillTool(home=aldwin_home, host_flags={})
    out = await tool.forward(
        "fail-skill",
        "Fail Load when testing.",
        "1. Nope.\n",
    )
    assert out.ok is False
    assert str(out).startswith("failed:")
    assert "Skill failed" in (out.summary or "")


def test_validate_rejects_non_string_args() -> None:
    with pytest.raises(ToolCallError, match=re.escape(INVALID_DESCRIPTION)):
        validate_create_skill_args("weekly-report", 123, "body")  # type: ignore[arg-type]
    with pytest.raises(ToolCallError, match="1-20000 characters"):
        validate_create_skill_args("weekly-report", "Desc Load when.", 99)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_atomic_write_cleans_temp_on_failure(
    aldwin_home: Path, monkeypatch
) -> None:
    from aldwin.core.tools import skill_tools

    real_replace = skill_tools.os.replace

    def _fail_replace(src: Path | str, dst: Path | str) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(skill_tools.os, "replace", _fail_replace)
    tool = CreateSkillTool(home=aldwin_home, host_flags={})
    out = await tool.forward(
        "tmp-fail",
        "Tmp fail Load when testing.",
        "1. Body.\n",
    )
    assert out.ok is False
    parent = aldwin_home / "skills" / "tmp-fail"
    leftovers = list(parent.glob("*.aldwin-tmp")) if parent.exists() else []
    assert leftovers == []
    monkeypatch.setattr(skill_tools.os, "replace", real_replace)
