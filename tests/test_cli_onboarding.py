"""Unit and integration tests for first-run onboarding and `aldwin setup`."""

from __future__ import annotations

import io
from pathlib import Path

import pytest

from aldwin.cli.onboarding import needs_onboarding, run_onboarding, run_setup
from aldwin.cli.theme import create_console
from aldwin.client import KeyCheckResult, bootstrap, get_onboarding_status


async def test_onboarding_bad_key_then_good_key(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-02: Rejected key shows error copy, then valid key shows ✓, model, and tips."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    boot = bootstrap(plain=True)
    assert boot.created is True
    assert needs_onboarding(boot) is True

    answers = iter(["sk-bad-key", "sk-proj-12345678good", ""])
    password_flags: list[bool] = []

    async def fake_prompt(_label: str, is_password: bool) -> str:
        password_flags.append(is_password)
        return next(answers)

    async def fake_checker(key: str) -> KeyCheckResult:
        return "ok" if key.endswith("good") else "invalid"

    buf = io.StringIO()
    console = create_console(plain=True, width=80, record=True, file=buf)
    ok = await run_onboarding(
        boot,
        console,
        prompt_fn=fake_prompt,
        key_checker=fake_checker,
    )

    assert ok is True
    assert password_flags == [True, True, True]
    text = console.export_text()
    assert "Welcome to Aldwin" in text
    assert "That key was rejected by OpenAI. Check it and paste it again." in text
    assert "Checking key… ✓" in text
    assert "Default model: gpt-4o (change any time with /model)" in text
    assert f"Created {boot.display_home}" in text
    assert 'Try: "what can you do?"  ·  /help  ·  Esc stops an answer' in text

    status = get_onboarding_status(aldwin_home)
    assert status.has_openai_key is True
    assert status.openai_source == "file"
    assert status.masked_openai_key == "sk-…good"
    assert ((aldwin_home / "secrets.yaml").stat().st_mode & 0o777) == 0o600


async def test_onboarding_env_skip_error_variants_and_three_strikes(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-16: Valid env key skips prompt; error copy & 3-strike exit."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-valid-12345678")
    boot = bootstrap(plain=True)

    prompt_called = False

    async def should_not_prompt(_label: str, _is_password: bool) -> str:
        nonlocal prompt_called
        prompt_called = True
        return ""

    async def env_ok_checker(_key: str) -> KeyCheckResult:
        return "ok"

    buf_env = io.StringIO()
    console_env = create_console(plain=True, width=80, record=True, file=buf_env)
    ok_env = await run_onboarding(
        boot,
        console_env,
        prompt_fn=should_not_prompt,
        key_checker=env_ok_checker,
    )
    assert ok_env is True
    assert prompt_called is False
    assert f"Created {boot.display_home}" in console_env.export_text()

    # Invalid env key prints error, prompts interactively, and updates os.environ
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-broken")
    env_fallback_inputs = iter(["sk-env-fixed-99999999", ""])

    async def env_fallback_prompt(_label: str, _is_password: bool) -> str:
        return next(env_fallback_inputs)

    async def env_fallback_checker(key: str) -> KeyCheckResult:
        return "ok" if "fixed" in key else "invalid"

    buf_env_fallback = io.StringIO()
    console_env_fallback = create_console(
        plain=True, width=80, record=True, file=buf_env_fallback
    )
    assert (
        await run_onboarding(
            boot,
            console_env_fallback,
            prompt_fn=env_fallback_prompt,
            key_checker=env_fallback_checker,
        )
        is True
    )
    assert "OPENAI_API_KEY in environment failed" in console_env_fallback.export_text()
    assert get_onboarding_status(aldwin_home).masked_openai_key == "sk-…9999"

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    results: list[KeyCheckResult] = ["no_access", "network_error", "invalid"]
    results_iter = iter(results)
    inputs = iter(["sk-no-access", "sk-net-err", ""])

    async def strike_prompt(_label: str, _is_password: bool) -> str:
        return next(inputs)

    async def strike_checker(_key: str) -> KeyCheckResult:
        return next(results_iter)

    buf_fail = io.StringIO()
    console_fail = create_console(plain=True, width=80, record=True, file=buf_fail)
    ok_fail = await run_onboarding(
        boot,
        console_fail,
        prompt_fn=strike_prompt,
        key_checker=strike_checker,
    )
    assert ok_fail is False
    fail_text = console_fail.export_text()
    assert "That key doesn't have access to OpenAI chat models." in fail_text
    assert "Can't reach OpenAI to verify your key." in fail_text
    assert "That key was rejected by OpenAI." in fail_text
    assert "Could not verify an OpenAI key after 3 attempts." in fail_text
    assert "aldwin setup" in fail_text


async def test_setup_command_updates_keys_and_model(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-17: run_setup updates OpenAI key, Tavily key, and default model."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    # First run setup with initial keys and model choice 2 ("gpt-4.1")
    prompts_seen: list[str] = []
    answers_first = iter(["sk-old-11111111", "tvly-dev-22222222", "2"])

    async def prompt_first(label: str, _is_password: bool) -> str:
        prompts_seen.append(label)
        return next(answers_first)

    async def checker_ok(_key: str) -> KeyCheckResult:
        return "ok"

    async def lister(_key: str | None, _home: Path | None) -> list[str]:
        return ["gpt-4o", "gpt-4.1", "o3-mini"]

    buf1 = io.StringIO()
    console1 = create_console(plain=True, width=80, record=True, file=buf1)
    assert (
        await run_setup(
            boot,
            console1,
            prompt_fn=prompt_first,
            key_checker=checker_ok,
            model_lister=lister,
        )
        is True
    )

    status1 = get_onboarding_status(aldwin_home)
    assert status1.masked_openai_key == "sk-…1111"
    assert status1.masked_tavily_key == "tvly-…2222"
    assert status1.model == "gpt-4.1"

    # Second run: press Enter to keep existing OpenAI key, update model to o3-mini
    prompts_second: list[str] = []
    answers_second = iter(["", "", "3"])

    async def prompt_second(label: str, _is_password: bool) -> str:
        prompts_second.append(label)
        return next(answers_second)

    buf2 = io.StringIO()
    console2 = create_console(plain=True, width=80, record=True, file=buf2)
    assert (
        await run_setup(
            boot,
            console2,
            prompt_fn=prompt_second,
            key_checker=checker_ok,
            model_lister=lister,
        )
        is True
    )
    assert any("sk-…1111" in p for p in prompts_second)
    status2 = get_onboarding_status(aldwin_home)
    assert status2.masked_openai_key == "sk-…1111"
    assert status2.model == "o3-mini"
    assert "#" in (aldwin_home / "config.yaml").read_text(encoding="utf-8")
