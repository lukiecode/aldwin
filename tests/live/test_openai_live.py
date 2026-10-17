"""Opt-in live OpenAI smoke tests (`uv run pytest -m live`)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from agentling import FinalEvent
from pydantic import SecretStr

from aldwin.core.agent import build_agent, new_session
from aldwin.core.home import ensure_home
from aldwin.core.models import KeyStatus, check_key, list_chat_models
from aldwin.core.settings import AldwinConfig, SecretsConfig


async def _deny_ask(_summary: str, _detail: str = "") -> bool:
    return False


@pytest.mark.live
async def test_openai_live_key_models_and_turn(aldwin_home: Path) -> None:
    """TC-12: Real OpenAI API key check, model list, and 1-turn streamed answer."""
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        pytest.skip("OPENAI_API_KEY not set in environment.")

    ensure_home(aldwin_home)

    status = await check_key(api_key)
    assert status == KeyStatus.OK

    models = await list_chat_models(api_key)
    assert models
    assert any(m.startswith("gpt-4o") for m in models)

    agent = build_agent(
        AldwinConfig(model="gpt-4o-mini"),
        SecretsConfig(openai_api_key=SecretStr(api_key)),
        _deny_ask,
        home=aldwin_home,
    )
    session = new_session(agent)

    final_event: FinalEvent | None = None
    async for ev in session.run("Reply with the single word pong.", stream=True):
        if isinstance(ev, FinalEvent):
            final_event = ev

    assert final_event is not None
    assert final_event.status == "completed"
    assert "pong" in final_event.answer.lower()
