# AGENTS.md — prompt-coach

Canonical guidance for AI coding agents working on this repo. (Project vision and
full spec are in `BLUEPRINT.md`.)

> **This is the only agent guidance file.** Do not create `CLAUDE.md`, `.cursorrules`,
> `.github/copilot-instructions.md`, or any other agent-specific config files. If you
> need to add or correct guidance, edit `AGENTS.md` (or the nearest `AGENTS.md` in a
> subdirectory).

## What This Is

A **local-first, privacy-preserving prompt coach** — a CLI tool that reads your
conversation history from local session stores (Hermes SQLite, OpenWebUI, etc.),
analyses your personal prompting patterns via a local LLM, and produces insights
reports. Zero data ever leaves the machine.

Adjacent module to the `llm-api` project (`~/projects/llm-api`). Shares conventions
and can optionally ingest usage logs from the gateway.

## Quick Start

```bash
uv sync                              # install deps
uv run prompt-coach discover         # find all available session stores
uv run prompt-coach report           # generate insights from the most recent sessions
uv run prompt-coach report --since 7d  # last 7 days
uv run prompt-coach report --model qwen3-coder:30b  # specify local model
```

## Stack

- Python 3.12+, **uv** (deps/venv), FastAPI (small read-only API for session ingestion)
- **openai** Python client → local Ollama (via llm-api or direct)
- SQLAlchemy + aiosqlite for reading Hermes session DBs
- httpx for OpenWebUI/other HTTP-based stores
- **ruff** (lint), **black** (format), **pytest**
- Source layout under `src/prompt_coach/` (standard src-layout)

## Commands

```bash
uv sync                  # install deps
uv run pytest            # unit + integration tests
uv run ruff check .      # lint
uv run black --check .   # format
uv run prompt-coach --help
```

## Conventions

- **Folder names**: lowercase-hyphenated. No PascalCase, underscores, or spaces.
- **AGENTS.md is canonical** — no CLAUDE.md, .cursorrules, etc.
- **One task at a time** — when implementing, finish one functional piece before
  starting the next.
- **Tests before merge** — every component needs tests.
- **Em dashes** — do not use them. Replace with a hyphen, colon, or rephrase.

## Integration with llm-api

This repo lives alongside `~/projects/llm-api/` at `~/projects/prompt-coach/`.
They are separate repositories (separate `pyproject.toml`, separate venv). Cross-references:

- `prompt-coach` can ingest `llm-api`'s usage logs (JSON) for analytics
- `prompt-coach` can talk to local models through `llm-api`'s `/v1/chat/completions` endpoint
  (falling back to direct Ollama if the gateway isn't running)
- Shared conventions: ruff config, black config, test structure, src-layout

## Key References

- `BLUEPRINT.md` — full project design, validated gap, architecture, implementation tasks
- `~/projects/hermes-skills/shared/learnings.md` — user's conventions, preferences, project context
