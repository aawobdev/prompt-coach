# AGENTS.md - prompt-coach

Canonical guidance for AI coding agents working on this repo. (Project vision
and full spec are in `BLUEPRINT.md`; progress in `STATUS.md`; decision history
in `DECISIONS.md`.)

> **This is the only agent guidance file.** Do not create `CLAUDE.md`,
> `.cursorrules`, `.github/copilot-instructions.md`, or any other
> agent-specific config files. If you need to add or correct guidance, edit
> `AGENTS.md` (or the nearest `AGENTS.md` in a subdirectory).
>
> The Claude Code plugin under `plugin/` (with `.claude-plugin/marketplace.json`)
> is a shipped product, not agent guidance, so it is not covered by this rule.

## What This Is

A **local-first, privacy-preserving prompt coach**: a CLI tool that reads your
conversation history from local session stores, analyses your personal
prompting patterns (deterministic metrics + prompting-standards A1-A13 rubric
+ local-LLM pattern detection), and produces coaching reports. Zero prompt
content ever leaves the machine.

Adjacent module to the `llm-api` project (`~/projects/llm-api`). Shares
conventions (uv, ruff, black, pytest, src-layout, STATUS/DECISIONS docs).

## Session stores (verified facts, do not trust older docs)

- **Hermes**: `~/.hermes/state.db` (SQLite, WAL). Tables `sessions` and
  `messages`; extract `role='user' AND active=1 AND compacted=0`. There is
  NO `profiles` table and NO `~/.hermes/data/sessions.db` (an earlier
  blueprint invented both). Open read-only via `file:...?mode=ro`.
- **Claude Code**: `~/.claude/projects/<slug>/*.jsonl`. Keep lines where
  `type=user`, `origin.kind=human`, `promptSource in (typed, sdk)`, not `isSidechain`,
  and content is a real prompt (drop command echoes and system reminders).
- **Copilot Chat**: `/mnt/c/.../workspaceStorage/<ws>/chatSessions/*.jsonl`
  (JSON-patch event log; verified live 2026-07-20).
- **Codex CLI**: `~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl` (`/mnt/c` on
  WSL, native path as fallback; verified live 2026-07-21). Keep
  `type=event_msg` lines with `payload.type=user_message` and
  `payload.kind=plain`; `kind=environment_context` is injected boilerplate,
  never something typed. The VS Code extension wraps real requests in an
  IDE-context block; strip down to the text after the
  `## My request for Codex:` marker when present.
- **JSON import**: list of sessions with `messages:[{role, content}]`.
- Hermes "user" rows include machine-generated `hermes -z` task specs
  (`TASK:` prefixes): classify origin human/machine and segment all analysis.
- **Never write to any source store.** All derived state lives in
  `~/.cache/prompt-coach/` and is disposable.

## Quick Start

```bash
uv sync                                  # install deps
uv run prompt-coach discover             # list session stores with counts
uv run prompt-coach report --since 7d    # coaching report, recent history
uv run prompt-coach report --no-llm      # deterministic-only (Ollama off)
uv run prompt-coach query "what did I work on last week?"
```

## Stack

- Python 3.12+, **uv** (deps/venv), Typer CLI, src-layout under
  `src/prompt_coach/`
- Stdlib `sqlite3` for all DB access (read-only sources + local cache DB
  with FTS5). Synchronous code throughout: no ORM, no asyncio.
- **openai** sync client to a local model only: direct Ollama
  (`http://192.168.1.123:11434/v1`, default `qwen3-coder-30b:latest`, 32k num_ctx) or the
  llm-api gateway on localhost. The LLM client refuses non-private base URLs
  unless `allow_remote = true` is configured: the privacy guarantee depends
  on it.
- **ruff** (lint, incl. `S` security rules), **black** (format), **pytest**
  (+respx for HTTP mocking).
- `server/` is an empty placeholder; the read-only HTTP API is phase 2.

## Commands

```bash
uv sync --extra dev      # install deps, including pytest/ruff/black (bare `uv sync` omits them)
uv run pytest            # unit + integration tests
uv run ruff check .      # lint
uv run black --check .   # format
uv run prompt-coach --help
```

## Agent Integration (for LLM agents reading this)

When a user asks about their prompting quality, streaks, scores, or the audit
trail, use `prompt-coach` CLI commands with `--json` output and present the
results conversationally:

### Score a prompt on demand

When the user is about to send a prompt and asks "is this any good?":

```bash
prompt-coach score "their prompt text" --json
```

Returns: `{"score": 75, "bonuses": [["Output format specified", 15], ...], "penalties": [...]}`

Present it as a readable breakdown, e.g. "That prompt scores 75/100: it's
missing an output format and a worked example, which would give you more
targeted results."

### Show streaks and trends

```bash
prompt-coach streaks --json
```

Returns: `{"good_prompt_streak": 12, "no_intervention_streak": 8, "best_streak": 42}`

### Audit trail (what got nudged/improved)

```bash
prompt-coach audit --since 7d --json             # summary by mode
prompt-coach audit --since 7d --detail --json    # individual entries
```

### Coaching report

```bash
prompt-coach report --since 30d --no-llm         # text report (already human-readable)
```

### Quick stats

```bash
prompt-coach stats --since 30d                   # metrics overview
```

### Respond in the user's language

If the user writes in a non-English language, present the output in the same
language. Keep it concise: a score, a short breakdown, and one actionable
suggestion is better than a wall of numbers.

## Nudge modes

The tool has three live nudge modes (plus "off"). When prompting someone about
which mode to use:

- **coach**: blocks weak prompts before they send, offers a rewrite. Best for
  learning / deliberate improvement.
- **auto**: silently improves weak prompts via a self-improve directive. Best
  for speed: you see the refined prompt and the answer in one response.
- **hint**: lets the prompt through, shows a tip after the response. Non-intrusive,
  purely educational.
- **always**: blocks and rewrites every prompt unconditionally. Explicit opt-in.
- **off**: no intervention.

Configure in `~/.config/prompt-coach/config.toml` under `[nudge] mode`.

## Per-directory overrides

```toml
[nudge.dir_overrides]
"/home/user/projects/my-app" = "coach"
"/home/user/projects/scratch" = "off"
```

## Conventions

- **Folder names**: lowercase-hyphenated. No PascalCase, underscores, spaces.
- **AGENTS.md is canonical**: no CLAUDE.md, .cursorrules, etc.
- **One task at a time**: work `BLUEPRINT.md` build tasks in dependency
  order, run each task's Verify command, update `STATUS.md`, then move on.
- **Tests before merge**: every component needs tests.
- **Em dashes**: do not use them anywhere in this repo. Replace with a
  hyphen, colon, or rephrase.
- **Privacy**: never log or print prompt content in diagnostics; counts and
  hashes only. Never add a cloud API key to config.

## Integration with llm-api

Separate repos, shared conventions. `prompt-coach` can talk to local models
through llm-api's `/v1/chat/completions` on localhost, but only when that
gateway's routing is pinned to local providers (it can fall back to
OpenRouter, which would leak prompt content; the client guard exists for
this). Usage-log ingestion from llm-api is deferred to phase 2.

## Key References

- `BLUEPRINT.md`: full spec, corrected store schemas, build plan T1-T34
- `STATUS.md` / `DECISIONS.md`: progress and decision history
- `~/projects/hermes-skills/skills/prompting-standards/SKILL.md`: the A1-A13
  rubric the analysis scores against
- `~/projects/hermes-skills/shared/learnings.md`: user conventions and context
