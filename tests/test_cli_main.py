"""CliRunner tests for `aldwin`, `aldwin setup`, and `aldwin uninstall` (`FR-1`)."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

import aldwin.cli.main as cli_main
import aldwin.cli.onboarding as onboarding_mod
from aldwin import __version__
from aldwin.cli.main import app
from aldwin.client import BootstrapResult, get_onboarding_status

runner = CliRunner()


@pytest.mark.uninitialized_home
def test_cli_main_routes_to_chat_and_version(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-01: Bare `aldwin` routes to chat entry; `--version` prints version."""
    ver_res = runner.invoke(app, ["--version"])
    assert ver_res.exit_code == 0
    assert ver_res.output.strip() == f"aldwin {__version__}"
    assert not aldwin_home.exists()

    routed: list[BootstrapResult] = []

    async def fake_launch(result: BootstrapResult, *, debug: bool = False) -> int:
        _ = debug
        routed.append(result)
        return 0

    monkeypatch.setattr(cli_main, "_is_interactive_tty", lambda: True)
    monkeypatch.setattr(cli_main, "_launch_interactive_session", fake_launch)

    chat_res = runner.invoke(app, ["--plain"])
    assert chat_res.exit_code == 0, chat_res.output
    assert len(routed) == 1
    assert routed[0].home == aldwin_home.resolve()
    assert routed[0].plain is True


def test_uninstall_keeps_data_and_purge_requires_double_confirmation(
    aldwin_home: Path,
) -> None:
    """TC-18: uninstall keeps data; --purge requires both y/n and folder name."""
    # Populate aldwin_home
    runner.invoke(app, [])
    assert aldwin_home.exists()
    assert (aldwin_home / "config.yaml").is_file()

    # (a) `aldwin uninstall` without --purge keeps data and prints uv command
    keep_res = runner.invoke(app, ["uninstall"])
    assert keep_res.exit_code == 0, keep_res.output
    assert "uv tool uninstall aldwin" in keep_res.output
    assert aldwin_home.exists()

    # (b1) `aldwin uninstall --purge` declined at y/n confirmation
    decline_res = runner.invoke(app, ["uninstall", "--purge"], input="n\n")
    assert decline_res.exit_code == 1
    assert aldwin_home.exists()

    # (b2) `aldwin uninstall --purge` with wrong folder name
    wrong_name_res = runner.invoke(
        app,
        ["uninstall", "--purge"],
        input="y\nwrong-folder-name\n",
    )
    assert wrong_name_res.exit_code == 1
    assert "did not match" in wrong_name_res.output
    assert aldwin_home.exists()

    # (c) `aldwin uninstall --purge` with y and exact folder name deletes home
    purge_res = runner.invoke(
        app,
        ["uninstall", "--purge"],
        input=f"y\n{aldwin_home.name}\n",
    )
    assert purge_res.exit_code == 0, purge_res.output
    assert "uv tool uninstall aldwin" in purge_res.output
    assert not aldwin_home.exists()


def test_setup_cli_command_end_to_end(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TC-17 (CLI integration): `aldwin setup` saves keys and model via CliRunner."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    async def fake_check(_key: str) -> onboarding_mod.KeyCheckResult:
        return "ok"

    async def fake_models(_key: str | None, _home: Path | None) -> list[str]:
        return ["gpt-4o", "gpt-4.1"]

    monkeypatch.setattr(onboarding_mod, "check_openai_key", fake_check)
    monkeypatch.setattr(onboarding_mod, "list_available_models", fake_models)

    res = runner.invoke(
        app,
        ["setup", "--plain"],
        input="sk-proj-setup9999\ntvly-dev-setup8888\n2\n",
    )
    assert res.exit_code == 0, res.output
    status = get_onboarding_status(aldwin_home)
    assert status.masked_openai_key == "sk-…9999"
    assert status.masked_tavily_key == "tvly-…8888"
    assert status.model == "gpt-4.1"


def test_non_tty_graceful_exit(aldwin_home: Path) -> None:
    """TC-25 (part 2): Non-TTY invocation exits 0 cleanly with no traceback."""
    res = runner.invoke(app, [])
    assert res.exit_code == 0
    assert "Traceback" not in res.output
    assert "interactive terminal" in res.output
    assert aldwin_home.exists()


async def test_launch_interactive_session_onboarding_and_chat(
    aldwin_home: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_launch_interactive_session` runs onboarding when needed, then `run_chat`."""
    import aldwin.cli.chat_screen as chat_mod
    from aldwin.client import bootstrap, save_setup

    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    boot = bootstrap(plain=True)

    # Case 1: onboarding fails -> returns exit code 1
    async def fail_onboarding(*_args: object, **_kwargs: object) -> bool:
        return False

    monkeypatch.setattr(onboarding_mod, "run_onboarding", fail_onboarding)
    assert await cli_main._launch_interactive_session(boot, debug=False) == 1

    # Case 2: key present -> skips onboarding and runs run_chat(skip_welcome=False)
    save_setup(openai_api_key="sk-proj-ready1234", home=aldwin_home)
    seen_skip_welcome: list[bool] = []

    async def fake_run_chat(
        *_args: object,
        skip_welcome: bool = False,
        **_kwargs: object,
    ) -> int:
        seen_skip_welcome.append(skip_welcome)
        return 0

    monkeypatch.setattr(chat_mod, "run_chat", fake_run_chat)
    assert await cli_main._launch_interactive_session(boot, debug=True) == 0
    assert seen_skip_welcome == [False]

    # Case 3: onboarding runs -> passes skip_welcome=True to avoid duplicate panel
    (aldwin_home / "secrets.yaml").unlink()

    async def ok_onboarding(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(onboarding_mod, "run_onboarding", ok_onboarding)
    assert await cli_main._launch_interactive_session(boot, debug=False) == 0
    assert seen_skip_welcome == [False, True]


def test_cli_help_has_no_internal_ticket_tags() -> None:
    """TC-13: `aldwin --help` describes `setup` and `uninstall` without `FR-1`."""
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    assert "setup" in res.output
    assert "uninstall" in res.output
    assert "FR-1" not in res.output
