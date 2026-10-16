"""Typer command-line entry point for Aldwin (`aldwin.cli.main:app`)."""

from __future__ import annotations

import typer

from aldwin import __version__
from aldwin.client import (
    ClientBootstrapError,
    PlatformNotSupportedError,
    bootstrap,
)

app = typer.Typer(
    name="aldwin",
    help="Aldwin: a personal assistant that runs on your computer.",
    no_args_is_help=False,
    rich_markup_mode="rich",
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"aldwin {__version__}")
        raise typer.Exit(code=0)


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
    _ = (version, plain)
    if ctx.invoked_subcommand is not None:
        return

    try:
        result = bootstrap(debug=debug)
    except (PlatformNotSupportedError, ClientBootstrapError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    if result.created:
        typer.echo(f"Created {result.display_home}")

    typer.echo(f"Aldwin {__version__} is ready (model: {result.model}).")


@app.command()
def setup() -> None:
    """Configure API keys and default model."""
    typer.echo("Interactive setup arrives in ENG-383.")


@app.command()
def uninstall(
    purge: bool = typer.Option(
        False,
        "--purge",
        help="Also delete ~/.aldwin and all saved data.",
    ),
) -> None:
    """Uninstall Aldwin (keeps ~/.aldwin unless --purge is passed)."""
    _ = purge
    typer.echo("Uninstall arrives in ENG-383.")


if __name__ == "__main__":
    app()
