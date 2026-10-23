"""Installer detection heuristics for uninstall."""

from __future__ import annotations

from aldwin.cli.install_detect import detect_install_method, uninstall_command


def test_detect_uv_pipx_pip_unknown() -> None:
    assert (
        detect_install_method(path_blob="/Users/x/.local/share/uv/tools/aldwin") == "uv"
    )
    assert detect_install_method(path_blob="/home/x/.local/pipx/venvs/aldwin") == "pipx"
    assert (
        detect_install_method(
            path_blob="/home/x/proj/.venv/lib/python3.12/site-packages"
        )
        == "pip"
    )
    assert detect_install_method(path_blob="/usr/bin/python3") == "unknown"


def test_uninstall_command_strings() -> None:
    assert uninstall_command("uv") == "uv tool uninstall aldwin"
    assert uninstall_command("pipx") == "pipx uninstall aldwin"
    assert uninstall_command("pip") == "pip uninstall aldwin"
    assert uninstall_command("unknown") == "uv tool uninstall aldwin"


def test_detect_install_method_reads_live_paths() -> None:
    """Without path_blob, detection still returns a known InstallMethod."""
    assert detect_install_method() in ("uv", "pipx", "pip", "unknown")
