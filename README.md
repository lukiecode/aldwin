# Aldwin

An open-source personal assistant that runs on your own computer, in your terminal, built on [Agentling](https://agentling.dev).

![Aldwin 0.0.1 session: install, first run, tool call and command approval](https://raw.githubusercontent.com/lunarstack-co/aldwin/main/docs/assets/demo.gif)

---

## Install

Install with [uv](https://docs.astral.sh/uv/) (Python 3.11+ on macOS or Linux):

```bash
uv tool install aldwin
```

If you don't have `uv` installed yet:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then start Aldwin:

```bash
aldwin
```

---

## First run

Nothing runs during `uv tool install aldwin`. The first time you run `aldwin`, it sets up your private home folder at `~/.aldwin` (`0700` permissions) and walks through a 30-second setup:

1. **OpenAI API key:** prompts for your key with hidden input (`••••`), tests it against OpenAI (`Checking key… ✓`), and saves it to `~/.aldwin/secrets.yaml` (`0600`). If `OPENAI_API_KEY` is already set in your environment, Aldwin uses it automatically and skips this prompt.
2. **Tavily API key (optional):** prompts for a [Tavily](https://tavily.com) key for `web_search` (press `Enter` to skip; you can add one later with `aldwin setup`).
3. **Default model:** saves a default OpenAI chat model (`gpt-4o`, changeable any time with `/model`) and opens the chat prompt.

Every time you run `aldwin` after that, it shows your last 20 messages and continues the conversation where you left off.

---

## Commands and slash commands

### Terminal commands

| Command | What it does |
|---|---|
| `aldwin` | Open the interactive chat and resume the latest conversation |
| `aldwin setup` | Configure or update API keys and the default model |
| `aldwin uninstall` | Print the uninstall command while keeping `~/.aldwin` intact |
| `aldwin uninstall --purge` | Delete `~/.aldwin` (after two confirmations) and print the uninstall command |
| `aldwin --plain` | Disable ANSI colours, live animations, and the terminal bell (also enabled by `NO_COLOR=1`) |
| `aldwin --debug` | Enable `DEBUG`-level event logging in `~/.aldwin/logs/aldwin.log` |
| `aldwin --version` | Print the installed version (`aldwin 0.0.1`) and exit |
| `aldwin --help` | Show CLI flags and subcommands |

### Slash commands (inside `aldwin`)

Type `/` at the prompt for autocomplete:

| Slash command | What it does |
|---|---|
| `/help` | Show slash commands and keyboard shortcuts |
| `/new` | Start a fresh conversation (previous session stays saved on disk) |
| `/model [name\|all]` | List available OpenAI chat models and switch models mid-conversation |
| `/persona` | Show the active persona and `~/.aldwin/persona.md` character count |
| `/memory` | Show `~/.aldwin/memory/profile.md` status and notes from the last 7 days |
| `/skills` | List installed skills in `~/.aldwin/skills/` and flag any invalid `SKILL.md` files |
| `/tools` | List built-in tools and which ones require `y/n` approval |
| `/exit` | Save the current session and exit |

### Keyboard shortcuts

| Key | Action |
|---|---|
| `Enter` | Send message |
| `Alt+Enter` / `Ctrl+J` / `\ + Enter` | Insert a new line |
| `Esc` | Stop the current answer within 1 second (or decline a pending approval) |
| `?` | Show keyboard shortcuts (when the prompt is empty) |
| `↑` / `↓` | Navigate input history |
| `Ctrl+R` | Reverse-search input history |
| `Ctrl+C` twice / `Ctrl+D` | Save the conversation and exit |

---

## What it can do

### Five built-in tools

Aldwin `0.0.1` ships with five user-facing tools (and one internal `set_persona` tool):

| Tool | What it does | Needs `y/n` approval? |
|---|---|---|
| `web_search` | Searches the web via Tavily for current information | No |
| `web_fetch` | Fetches and extracts readable text from an `http`/`https` page (≤ 20,000 chars) | No |
| `run_command` | Runs a shell command in the directory where you launched `aldwin` | **Always** |
| `remember` | Appends a single-line dated note to `~/.aldwin/memory/notes/YYYY-MM-DD.md` | No |
| `recall` | Searches your daily notes by keyword | No |

### Skills

Drop a folder containing a `SKILL.md` with YAML frontmatter (`name` and `description`) into `~/.aldwin/skills/<skill-name>/SKILL.md`:

```markdown
---
name: release-notes
description: Draft concise release notes from git history.
---
Write release notes grouped into Added, Fixed, and Known limitations.
```

Aldwin lists available skill names and descriptions in the system prompt and loads the full `SKILL.md` on demand. Ships with a starter `using-aldwin` skill.

### Memory and persona

- **Profile (`~/.aldwin/memory/profile.md`):** durable facts about you (up to 4,000 characters), always included in the system prompt. Only you can edit this file; the agent cannot modify it.
- **Daily notes (`~/.aldwin/memory/notes/YYYY-MM-DD.md`):** short one-line notes saved when Aldwin calls `remember`. Notes from the last 7 days (up to 4,000 characters, newest first) are included in the prompt and age out after a week.
- **Persona (`~/.aldwin/persona.md`):** controls Aldwin's tone and style (up to 2,000 characters). You can edit the file directly or ask Aldwin to change how it talks (for example, *"Be more direct and concise"*), which shows the proposed `persona.md` in an approval box and asks `y/n` before saving.

---

## Safety model

Web pages can contain prompt-injection text, and `0.0.1` runs commands directly on your machine without an OS sandbox. Aldwin defends against this with layered guardrails in `aldwin.core` and `aldwin.host`:

1. **Every command asks `y/n` first:** `run_command` never executes without showing you the exact command string and waiting for `y`. Pressing `n`, `Enter`, or `Esc` declines the command and tells the agent you declined.
2. **Dangerous and interactive commands refused up front:** `sudo`, `su`, `doas`, interactive editors/pagers (`vim`, `nano`, `less`, `top`, `ssh`), and bare REPLs are refused before an approval prompt is even shown.
3. **Subprocess isolation:** commands run with `stdin` closed (`/dev/null`), a 60-second timeout, a 10,000-character output cap, and secret environment variables (`*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`) stripped.
4. **Every persona change asks `y/n`:** `set_persona` requires explicit `y/n` approval before writing `~/.aldwin/persona.md`, and the agent can never write to `config.yaml`, `secrets.yaml`, `memory/profile.md`, `sessions/`, or `skills/`.
5. **Untrusted web content & SSRF protection:** `web_fetch` allows only `http`/`https`, resolves DNS on every hop to block `localhost`, loopback (`127.0.0.0/8`, `::1`), RFC 1918 private networks, and cloud metadata IPs (`169.254.169.254`), caps downloads at 2 MB / 10 seconds, and wraps extracted text in `<untrusted source="...">` tags with system instructions to treat page content strictly as data, never instructions.

---

## Data and privacy

All local data lives in one directory, `~/.aldwin` (override with `ALDWIN_HOME=/path/to/dir`):

```text
~/.aldwin/                  (mode 0700)
├── config.yaml             model, theme, and character/step limits
├── secrets.yaml            OpenAI and Tavily API keys (mode 0600)
├── persona.md              assistant persona (user-editable or y/n approved)
├── skills/
│   └── using-aldwin/
│       └── SKILL.md        starter skill
├── sessions/
│   └── YYYY-MM-DD_id.json  conversation transcripts saved after every step
├── memory/
│   ├── profile.md          user-written profile (never edited by the agent)
│   └── notes/
│       └── YYYY-MM-DD.md   daily notes written by `remember`
├── history                 prompt_toolkit input history
└── logs/
    └── aldwin.log          rotating event/error log (1 MB × 3, no message content)
```

- **No telemetry:** Aldwin collects no analytics or telemetry.
- **No message content or secrets in logs:** `logs/aldwin.log` records lifecycle events and errors only, and passes all log records through a secret-redacting filter (`sk-…`, `tvly-…`).
- **Network calls:** your prompts and recent conversation turns (last 20 exchanges) are sent directly to OpenAI using your API key. When `web_search` or `web_fetch` runs, search queries go to Tavily and HTTP GET requests go to the target URL.

---

## Limitations in 0.0.1

- **Terminal only, not always-on:** Aldwin runs inside your terminal process. Closing the terminal window stops any running task (the session is saved after every step, and reopening `aldwin` prompts you to say *continue*).
- **OpenAI API key only:** requires an OpenAI API key with access to chat models. Other providers (Anthropic, local models) and browser OAuth sign-in are not in `0.0.1`.
- **Fixed built-in tools:** the five built-in tools cannot be extended with custom Python tools in `0.0.1`.
- **macOS and Linux only:** requires Python `3.11+` on macOS or Linux. Windows is not supported yet.

---

## Documentation

- [Protocol specification (`docs/protocol.md`)](https://github.com/lunarstack-co/aldwin/blob/main/docs/protocol.md)
- [Manual test script & release checklist (`docs/manual-test.md`)](https://github.com/lunarstack-co/aldwin/blob/main/docs/manual-test.md)
- [Release runbook (`docs/releasing.md`)](https://github.com/lunarstack-co/aldwin/blob/main/docs/releasing.md)
- [Changelog (`CHANGELOG.md`)](https://github.com/lunarstack-co/aldwin/blob/main/CHANGELOG.md)

---

## Contributing

Clone the repository and install development dependencies with `uv`:

```bash
git clone https://github.com/lunarstack-co/aldwin.git
cd aldwin
uv sync
```

Run the test suite, linter, formatter check, and type checker before opening a pull request:

```bash
uv run pytest --cov=aldwin
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src tests
```

Found a bug or want to propose an improvement? Open an issue on [GitHub Issues](https://github.com/lunarstack-co/aldwin/issues).

---

## Licence

MIT. See [`LICENSE`](https://github.com/lunarstack-co/aldwin/blob/main/LICENSE).
