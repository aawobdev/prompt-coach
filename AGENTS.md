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
prompting patterns (deterministic metrics + the A1-A13 prompting rubric in
`analysis/rubric.py` + LLM pattern detection), and produces coaching reports.
Prompt content stays on the machine unless the user explicitly allows a
remote LLM endpoint.

The tool is harness- and model-agnostic: one read-only reader per source
tool, and any OpenAI-compatible endpoint (or none) for the LLM features.

## Session stores (verified facts, do not trust older docs)

- **Hermes**: `~/.hermes/state.db` (SQLite, WAL). Tables `sessions` and
  `messages`; extract `role='user' AND active=1 AND compacted=0`. There is
  NO `profiles` table and NO `~/.hermes/data/sessions.db` (an earlier
  blueprint invented both). Open read-only via `file:...?mode=ro`.
- **Claude Code**: `~/.claude/projects/<slug>/*.jsonl`. Keep lines where
  `type=user`, `origin.kind=human`, `promptSource in (typed, sdk)`, not `isSidechain`,
  and content is a real prompt (drop command echoes, system reminders, and
  other harness wrapper tags -- see `is_harness_wrapped` in `stores/base.py`).
  `sdk` addition verified live 2026-09-24 against 277,872 real lines on this
  machine: `typed` appears in none of them (Claude Code's own wire format
  moved on; the old `typed`-only filter was accepting zero Claude Code
  prompts), `sdk` covers ordinary typed prompts too. `sdk` alone is not
  proof of human authorship though -- 143 of the newly-matched lines were
  harness-wrapped content (`<bash-input>`, `<artifact-view-context>`, etc.)
  that `origin.kind=human` doesn't catch either; `is_harness_wrapped`
  rejects those regardless of `promptSource`.
  Reply linking (verified live 2026-10-10): `type=attachment` lines (hook
  output, context) sit between a prompt and its reply, and the reply's
  `parentUuid` points at the last one; follow the parentUuid chain, never
  just the direct parent (4 of 2,171 recent prompts linked directly).
  Claude Code deletes transcripts after ~30 days, so its cached prompts are
  never purged on a parser bump (`purge_on_reparse = False`): the re-read
  fills changed fields in place.
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
- Hermes "user" rows are every turn the agent received, not just what was
  typed (verified live 2026-09-24, 2,829 rows). Rows from `cron` and
  `subagent` sessions are all machine (739 + 5). Slack/cli rows wrap the
  typed text in gateway metadata: `[Replying to: "<bot text>"]` (1,428),
  a thread-context replay (405), `[sam] ` speaker labels, attachment
  and model-switch notices, and whole injected `SKILL.md` bodies (126).
  `stores/hermes.py` `unwrap()` strips them; what's left starting
  `[System:` / `[IMPORTANT:` / compaction markers is the agent talking to
  itself (machine), and another speaker's label (`[robin]`) drops the row.
  `hermes -z` task specs (`TASK:`, `CARD n of n`) are machine too. Bump
  `HermesStore.parser_version` when this changes: the cache re-reads the
  store on a mismatch (a forced sync alone never rewrites cached rows).
- **Token usage (cost layer, verified live 2026-10-10)**:
  - Claude Code: every `type=assistant` line has `message.usage`. A streamed
    reply is written several times with the same `requestId` (51% of 137k
    usage lines here); key on (sessionId, requestId), last line wins (where
    duplicates differ, the last has the final output count). A resumed
    session copies calls into a second file with the same sessionId (1,221
    calls here), so per-file dedupe double counts. Sub-agents write
    `<project>/<session>/subagents/agent-*.jsonl` with the parent's
    sessionId: overhead, folded into the parent. `cache_creation` splits
    5-minute and 1-hour writes (1h bills at 2x input).
  - Hermes: `session_model_usage`, one row per (session, model, provider,
    task). `task != ''` (title_generation, background_review, vision,
    approval) is overhead; so are sessions with `parent_session_id`, folded
    into the parent. `estimated_cost_usd`/`actual_cost_usd` are used when
    `cost_status` is `estimated` (custom/local endpoints say `unknown`).
  - Copilot CLI (`~/.copilot/session-state/*/events.jsonl`): usage only,
    **unverified** -- built from a reference parser, no data on this machine.
  - Codex CLI, Copilot Chat, ChatGPT export, JSON import: no usage data.
- **Never write to any source store.** All derived state lives in
  `~/.cache/prompt-coach/` and is disposable.

## Quick Start

```bash
uv sync                                  # install deps
uv run prompt-coach discover             # list session stores with counts
uv run prompt-coach report --since 7d    # coaching report, recent history
uv run prompt-coach report --no-llm      # deterministic-only (no LLM needed)
uv run prompt-coach query "what did I work on last week?"
```

## Stack

- Python 3.12+, **uv** (deps/venv), Typer CLI, src-layout under
  `src/prompt_coach/`
- Stdlib `sqlite3` for all DB access (read-only sources + local cache DB
  with FTS5). Synchronous code throughout: no ORM, no asyncio.
- **openai** sync client against any OpenAI-compatible endpoint. There is no
  default endpoint or model (DECISIONS.md 2026-10-10): unset means
  deterministic-only, and every LLM caller must degrade cleanly. The client
  refuses non-private base URLs unless `allow_remote = true` is configured:
  the privacy guarantee depends on it. Provider-specific request fields go
  through `[llm] extra_body` (JSON), never hard-coded per vendor.
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
  hashes only. Never write a cloud API key into config: use `api_key_cmd`.

## Key References

- `BLUEPRINT.md`: original spec, corrected store schemas, build plan T1-T34
- `STATUS.md` / `DECISIONS.md`: progress and decision history
- `src/prompt_coach/analysis/rubric.py`: the A1-A13 rubric the analysis
  scores against
