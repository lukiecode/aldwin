# Aldwin

Aldwin is an open-source personal assistant that runs on your own computer, built on [Agentling](https://agentling.dev).

## Install

```bash
uv tool install aldwin
```

Then run:

```bash
aldwin
```

All data lives in `~/.aldwin` (override with `ALDWIN_HOME`).

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run mypy src tests
```

## License

MIT
