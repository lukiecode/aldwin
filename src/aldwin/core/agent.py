"""Agentling agent assembly, skill discovery, session factory, and model switching."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import yaml
from agentling import Agent, AgentSession, Memory, Model, Skill, Tool
from agentling.errors import SkillLoadError

from aldwin.core.context import build_context
from aldwin.core.home import home_dir, read_base_instructions
from aldwin.core.models import RetryCallback, build_openai_model
from aldwin.core.settings import (
    AldwinConfig,
    SecretsConfig,
    load_secrets,
    resolve_key,
)

logger = logging.getLogger("aldwin.core.agent")

SKILLS_SUBDIR: Final[str] = "skills"
SKILL_FILENAME: Final[str] = "SKILL.md"
DEFAULT_TOOL_TIMEOUT: Final[float] = 60.0
DEFAULT_MAX_TOOL_OUTPUT_CHARS: Final[int] = 10_000

_RESERVED_SKILL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "load_skill",
        "final_answer",
        "web_search",
        "web_fetch",
        "run_command",
        "remember",
        "recall",
        "set_persona",
    }
)

AskFn = Callable[[str, str], Awaitable[bool]]


async def _deny_all_ask(_summary: str, _detail: str = "") -> bool:
    """Default fail-closed approval callback when none is supplied."""
    return False


@dataclass(frozen=True, slots=True)
class InvalidSkill:
    """A skill folder in `~/.aldwin/skills/` that failed validation (`FR-5`)."""

    name: str
    path: Path
    reason: str


@dataclass(frozen=True, slots=True)
class SkillLoadResult:
    """Valid `Skill` objects and skipped `InvalidSkill` entries from `skills/`."""

    valid: list[Skill]
    invalid: list[InvalidSkill]


def load_base_instructions() -> str:
    """Return the package-shipped base instructions (`FR-3`)."""
    return read_base_instructions().strip()


def _clean_reason(exc: BaseException) -> str:
    """Format an exception into a single-line human reason for `/skills` and logs."""
    first_line = (
        str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    )
    return " ".join(first_line.split())


def load_skills(skills_dir: Path) -> SkillLoadResult:
    """Load and validate all skills under `skills_dir` (`FR-5`).

    Invalid skills are skipped with a one-line warning in the log and recorded in
    `SkillLoadResult.invalid` as `reason`. When two skills declare the same
    `name`, the first folder (in sorted order) wins and the duplicate is logged
    and recorded as invalid.
    """
    resolved_dir = skills_dir.expanduser().resolve()
    if not resolved_dir.is_dir():
        return SkillLoadResult(valid=[], invalid=[])

    valid: list[Skill] = []
    invalid: list[InvalidSkill] = []
    seen_names: set[str] = set()

    candidates = sorted(
        (
            p
            for p in resolved_dir.iterdir()
            if p.is_dir() and not p.name.startswith(".")
        ),
        key=lambda p: p.name,
    )

    for folder in candidates:
        skill_file = folder / SKILL_FILENAME
        if not skill_file.is_file():
            reason = f"missing {SKILL_FILENAME}"
            logger.warning("Skipping invalid skill in %s: %s", folder, reason)
            invalid.append(InvalidSkill(name=folder.name, path=folder, reason=reason))
            continue

        try:
            skill = Skill.from_path(folder)
            if not isinstance(skill.name, str) or not skill.name.strip():
                raise ValueError("frontmatter 'name' must be a non-empty string.")
            if not isinstance(skill.description, str) or not skill.description.strip():
                raise ValueError(
                    "frontmatter 'description' must be a non-empty string."
                )

            cleaned_name = skill.name.strip()
            if "\n" in cleaned_name or "\r" in cleaned_name:
                raise ValueError("frontmatter 'name' must be a single line.")
            skill.name = cleaned_name
            skill.description = " ".join(skill.description.split())

            if skill.tools:
                raise ValueError("custom skill tools are not supported in 0.0.1.")
            if skill.name in _RESERVED_SKILL_NAMES:
                raise ValueError(f"skill name {skill.name!r} is reserved.")
            if skill.name in seen_names:
                raise ValueError(f"duplicate skill name {skill.name!r}.")

            seen_names.add(skill.name)
            valid.append(skill)
        except (OSError, yaml.YAMLError, ValueError, TypeError, SkillLoadError) as exc:
            reason = _clean_reason(exc)
            logger.warning("Skipping invalid skill in %s: %s", folder, reason)
            invalid.append(InvalidSkill(name=folder.name, path=folder, reason=reason))

    return SkillLoadResult(valid=valid, invalid=invalid)


def _resolve_openai_key(
    secrets: SecretsConfig | None,
    home: Path,
) -> str | None:
    """Resolve the OpenAI API key from environment first, then `secrets` or disk."""
    resolved = resolve_key("openai", home)
    if resolved.key:
        return resolved.key
    if secrets is not None:
        direct = secrets.get_secret("openai")
        if direct:
            return direct
    loaded = load_secrets(home)
    return loaded.get_secret("openai")


def build_agent(
    config: AldwinConfig,
    secrets: SecretsConfig | None = None,
    ask: AskFn | None = None,
    *,
    home: Path | None = None,
    model: Model | None = None,
    tools: Sequence[Tool] | None = None,
    on_retry: RetryCallback | None = None,
) -> Agent:
    """Assemble Aldwin's `Agent` from `config`, `secrets`, and `ask` (`FR-1`)."""
    del ask  # Bound to built-in tools when ENG-381/ENG-382 wire `builtin_tools`.
    root = (home if home is not None else home_dir()).expanduser().resolve()

    if model is not None:
        active_model: Model = model
    else:
        api_key = _resolve_openai_key(secrets, root)
        active_model = build_openai_model(
            config.model,
            api_key=api_key,
            on_retry=on_retry,
        )

    skill_result = load_skills(root / SKILLS_SUBDIR)
    context_manager = build_context(root, config.limits)

    agent = Agent(
        model=active_model,
        tools=tuple(tools) if tools is not None else (),
        skills=skill_result.valid,
        instructions=load_base_instructions(),
        max_steps=config.limits.max_steps,
        tool_timeout=DEFAULT_TOOL_TIMEOUT,
        max_tool_output_chars=DEFAULT_MAX_TOOL_OUTPUT_CHARS,
        context_manager=context_manager,
    )
    setattr(agent, "invalid_skills", tuple(skill_result.invalid))
    return agent


def new_session(agent: Agent) -> AgentSession:
    """Create a fresh `AgentSession` from `agent` (`FR-2`)."""
    return agent.start()


def restore_session(agent: Agent, memory_json: str) -> AgentSession:
    """Create an `AgentSession` and restore its `Memory` from `memory_json` (`FR-2`)."""
    session = agent.start()
    session.memory = Memory.load_json(memory_json)
    return session


def rebuild_agent(
    config: AldwinConfig,
    secrets: SecretsConfig | None,
    ask: AskFn | None,
    model_name: str,
    *,
    home: Path | None = None,
    model: Model | None = None,
    tools: Sequence[Tool] | None = None,
    on_retry: RetryCallback | None = None,
) -> tuple[Agent, AldwinConfig]:
    """Rebuild the `Agent` for a new `model_name` (`FR-9`)."""
    cleaned = model_name.strip()
    if not cleaned:
        raise ValueError("model_name must not be empty.")
    updated_config = config.model_copy(update={"model": cleaned})
    agent = build_agent(
        updated_config,
        secrets,
        ask or _deny_all_ask,
        home=home,
        model=model,
        tools=tools,
        on_retry=on_retry,
    )
    return agent, updated_config


def switch_session_model(session: AgentSession, new_agent: Agent) -> AgentSession:
    """Create a session on `new_agent` carrying over `session.memory` (`FR-9`)."""
    updated_session = new_agent.start()
    updated_session.memory = Memory.from_dict(session.memory.to_dict())
    return updated_session
