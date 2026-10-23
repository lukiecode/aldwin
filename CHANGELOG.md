# Changelog

All notable changes to Aldwin are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Nine packaged default skills** (`using-aldwin`, `files`, `markdown-notes`, `documents`, `making-documents`, `web-research`, `memory`, `terminal`, `skill-creator`) loaded from the install via `importlib.resources`; user skills under `~/.aldwin/skills/<name>/` override by name.
- **`create_skill` tool** (Diff approval, never session-grantable) to write or update a user skill; host rebuilds the agent after the turn and shows `skills reloaded`.
- **`aldwin[docs]` optional extra** (`python-docx`, `openpyxl`, `reportlab`) for the `making-documents` skill.
- **`/skills show <name>`** prints the effective skill body; `/skills` and `aldwin skills` show `name · source · description` with `default` / `user` / `user override` labels.

### Changed

- Default skills are no longer copied into `~/.aldwin` on first run; upgrades improve packaged skills automatically.
- Skills never load code: a `tools:` frontmatter key is stripped (flagged as `tools removed` for user skills).

### Migration

- An unmodified 0.0.1 starter copy of `~/.aldwin/skills/using-aldwin/` is renamed to `using-aldwin.old-0.0.1/` so the new packaged identity block takes effect. An edited copy is kept as a user override.

## [0.0.1] - 2026-11-01

First public release of Aldwin: an open-source personal assistant that runs on your own computer, in your terminal, built on [Agentling](https://agentling.dev).

### Added

- **One-command install:** `uv tool install aldwin` installs the `aldwin` CLI (`aldwin`, `aldwin setup`, `aldwin uninstall [--purge]`, `--version`, `--plain`, `--debug`).
- **Private local home folder (`~/.aldwin`):** created on first run with mode `0700` (overridable via `ALDWIN_HOME`), atomic file writes (`fsync` + `os.replace`), rotating file logs (`logs/aldwin.log`, `1 MB × 3`) with secret redaction and no message content logged, and separate `secrets.yaml` (`0600`) with environment-variable priority (`OPENAI_API_KEY`, `TAVILY_API_KEY`).
- **First-run onboarding & setup:** guided terminal setup that prompts for an OpenAI API key with hidden input, verifies it against OpenAI, optionally configures a Tavily web search key, and saves a default chat model.
- **One brain, many doors architecture:** typed Pydantic v2 protocol (`aldwin.protocol`), in-process `asyncio.Queue` host (`aldwin.host`), client SDK (`aldwin.client`), and CLI door (`aldwin.cli`) with strict architectural isolation (`cli` never imports `core` or `agentling`).
- **Agentling + OpenAI core:** streaming responses, automatic exponential-backoff retries on transient OpenAI errors, dynamic context manager keeping the last 20 exchanges on clean turn boundaries, and user-extensible Markdown skills in `~/.aldwin/skills/<name>/SKILL.md` (shipping with the `using-aldwin` starter skill).
- **Five built-in tools:**
  - `web_search`: web search via Tavily behind a swappable `SearchProvider` interface.
  - `web_fetch`: HTTP/HTTPS page extraction via `httpx` + `trafilatura` (capped at 20,000 chars, wrapped as `<untrusted>` data, with DNS/IP SSRF blocking against localhost and private networks).
  - `run_command`: shell command execution in the launch directory with mandatory `y/n` user approval, pre-execution refusal of `sudo` and interactive programs, secret environment stripping (`*_KEY`, `*_TOKEN`, `*_SECRET`), 60-second timeout, and 10,000-char output cap.
  - `remember`: appends a single-line dated note with metadata to `~/.aldwin/memory/notes/YYYY-MM-DD.md` (last 7 days injected into context, up to 4,000 chars).
  - `recall`: keyword search across daily notes.
- **User-controlled memory and persona:** `~/.aldwin/memory/profile.md` (user-editable only, up to 4,000 chars) and `~/.aldwin/persona.md` (up to 2,000 chars, updated via `set_persona` only after explicit `y/n` user approval).
- **Session persistence & recovery:** conversations saved atomically to `~/.aldwin/sessions/<YYYY-MM-DD>_<id>.json` after every step; reopening `aldwin` displays the last 20 messages and resumes where you left off, with an interrupted-turn notice if the previous terminal closed mid-task.
- **Inline terminal UI:** built with Typer, Rich, and `prompt_toolkit` in native terminal scrollback, featuring fence-aware streaming Markdown, live spinner and status bar, tool cards, bordered approval prompts with terminal bell, `Esc` mid-turn stop within 1 second, `?` keyboard shortcut guide, plain mode (`--plain` / `NO_COLOR`), and slash commands (`/help`, `/new`, `/model`, `/persona`, `/memory`, `/skills`, `/tools`, `/exit`).

### Known limitations

- **Terminal only, not always-on yet:** Aldwin `0.0.1` runs inside your terminal process. Closing the terminal stops the active task (your conversation is saved after every step and resumes next time you run `aldwin`). Background daemon mode over `~/.aldwin/aldwin.sock` arrives in `0.1`.
- **OpenAI API key only:** requires your own OpenAI API key (`OPENAI_API_KEY` or configured on first run). Other model providers and account OAuth are not included in `0.0.1`.
- **Fixed built-in toolset:** includes the five built-in tools (`web_search`, `web_fetch`, `run_command`, `remember`, `recall`); custom code tools cannot be added in `0.0.1` (drop-in Markdown skills in `~/.aldwin/skills/` are supported).
- **macOS and Linux only:** requires Python `3.11+` on macOS or Linux. Windows is not supported yet.

[0.0.1]: https://github.com/lunarstack-co/aldwin/releases/tag/v0.0.1
