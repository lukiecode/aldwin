"""Typer command-line entry point for Aldwin (`aldwin.cli.main:app`)."""

from __future__ import annotations

import asyncio
import sys

import typer

from aldwin import __version__
from aldwin.client import (
    BootstrapResult,
    ClientBootstrapError,
    PlatformNotSupportedError,
    bootstrap,
    get_onboarding_status,
    purge_home_data,
)

app = typer.Typer(
    name="aldwin",
    help="Aldwin: a personal assistant that runs on your computer.",
    no_args_is_help=False,
    rich_markup_mode="rich",
    add_completion=False,
)


def _is_interactive_tty() -> bool:
    """Return `True` when `sys.stdin` is attached to an interactive terminal."""
    return sys.stdin.isatty()


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"aldwin {__version__}")
        raise typer.Exit(code=0)


async def _launch_interactive_session(
    result: BootstrapResult,
    *,
    debug: bool = False,
) -> int:
    """Run first-run onboarding if needed, then enter the interactive chat loop."""
    from aldwin.cli.chat_screen import run_chat
    from aldwin.cli.onboarding import needs_onboarding, run_onboarding
    from aldwin.cli.theme import create_console

    console = create_console(plain=result.plain)
    had_env_key = get_onboarding_status(result.home).openai_source == "env"
    skip_welcome = False
    if needs_onboarding(result):
        ok = await run_onboarding(result, console)
        if not ok:
            return 1
        skip_welcome = (
            not had_env_key
            or get_onboarding_status(result.home).openai_source == "file"
        )
    elif result.created:
        console.print(f"Created {result.display_home}", soft_wrap=True)

    return await run_chat(
        result,
        debug=debug,
        skip_welcome=skip_welcome,
        console=console,
    )


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    version: bool = typer.Option(
        False,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show the Aldwin version and exit.",
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Disable colours and live terminal animations.",
    ),
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Write debug-level events to ~/.aldwin/logs/aldwin.log.",
    ),
) -> None:
    """Launch Aldwin interactive chat (or run a subcommand)."""
    _ = version
    if ctx.invoked_subcommand is not None:
        return

    try:
        result = bootstrap(plain=plain, debug=debug)
    except (PlatformNotSupportedError, ClientBootstrapError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    if not _is_interactive_tty():
        if result.created:
            typer.echo(f"Created {result.display_home}")
        typer.echo(
            "Aldwin requires an interactive terminal (TTY). "
            f"Aldwin {__version__} is ready (model: {result.model})."
        )
        return

    exit_code = asyncio.run(_launch_interactive_session(result, debug=debug))
    if exit_code != 0:
        raise typer.Exit(code=exit_code)


@app.command()
def setup(
    plain: bool = typer.Option(
        False,
        "--plain",
        help="Disable colours and spinners during setup.",
    ),
) -> None:
    """Configure API keys and default model."""
    try:
        result = bootstrap(plain=plain, debug=False)
    except (PlatformNotSupportedError, ClientBootstrapError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    from aldwin.cli.onboarding import run_setup
    from aldwin.cli.theme import create_console

    console = create_console(plain=result.plain)
    ok = asyncio.run(run_setup(result, console))
    if not ok:
        raise typer.Exit(code=1)


@app.command()
def uninstall(
    purge: bool = typer.Option(
        False,
        "--purge",
        help="Also delete ~/.aldwin and all saved data.",
    ),
) -> None:
    """Uninstall Aldwin (keeps ~/.aldwin unless --purge is passed)."""
    try:
        result = bootstrap(plain=True, debug=False)
    except (PlatformNotSupportedError, ClientBootstrapError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    if purge:
        typer.echo(
            f"Warning: --purge will permanently delete {result.display_home} "
            "(all conversations, memory, persona, and API keys)."
        )
        confirmed = typer.confirm(
            f"Are you sure you want to delete {result.display_home}?",
            default=False,
        )
        if not confirmed:
            typer.echo("Aborted. Your data was not deleted.")
            raise typer.Exit(code=1)

        expected_names = {
            result.home.name,
            result.display_home,
            str(result.home),
        }
        typed = typer.prompt(
            f"Type '{result.home.name}' or '{result.display_home}' to confirm"
        )
        if typed.strip() not in expected_names:
            typer.echo("Folder name did not match. Aborted; your data was not deleted.")
            raise typer.Exit(code=1)

        purge_home_data(result.home)
        typer.echo(f"Deleted {result.display_home}.")
    else:
        typer.echo(f"Your data in {result.display_home} is untouched.")

    typer.echo("To remove the Aldwin command, run:\n  uv tool uninstall aldwin")


if __name__ == "__main__":
    app()
