---
name: using-aldwin
description: Explains what Aldwin can do, its built-in tools, local memory and persona files, and slash commands. Load when the user asks how Aldwin works or what it can do.
---

# Using Aldwin

Aldwin is a personal assistant that runs on your computer and stores all data locally in `~/.aldwin` (or `$ALDWIN_HOME`).

## Built-in tools

1. `web_search`: Search the web for current information (requires a Tavily API key).
2. `web_fetch`: Read a web page as clean text (capped and treated as untrusted data).
3. `run_command`: Run a shell command in the current working directory after explicit `y/n` approval.
4. `remember`: Save a short note to today's daily notes (`memory/notes/YYYY-MM-DD.md`).
5. `recall`: Search saved daily notes by keyword.

## Memory and persona

- `memory/profile.md`: Edited only by the user and included in every conversation (up to 4,000 characters).
- `memory/notes/YYYY-MM-DD.md`: Daily notes appended by `remember`. The last 7 days are included in the prompt (up to 4,000 characters), and older notes remain searchable via `recall`.
- `persona.md`: Controls how Aldwin communicates, updated only by the user or after `y/n` approval.

## Slash commands

- `/help`: Show available slash commands and keyboard shortcuts.
- `/new`: Start a fresh conversation (previous conversations stay in `sessions/`).
- `/model`: Switch the active OpenAI chat model.
- `/persona`: View the current persona.
- `/memory`: View profile status and recent daily notes.
- `/skills`: List installed skills in `skills/`.
- `/tools`: List the built-in tools.
- `/exit`: Save and quit Aldwin.
