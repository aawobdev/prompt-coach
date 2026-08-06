# prompt-coach

**Your personal prompting analyst. Runs locally. Zero data export.**

A CLI tool that reads your own prompt history from the session stores already
on your machine (Hermes `state.db`, Claude Code transcripts, Copilot Chat
sessions, Codex CLI sessions, ChatGPT data exports), analyses your prompting
style, and produces a coaching report: style metrics, scores against the
prompting-standards rubric, and LLM-detected patterns. Prompt content never
leaves your machine; analysis runs on a local Ollama model, and the tool
still works (deterministic analysis only) when no model is reachable.

## Quick start

Install as a real command on PATH (via `uv tool install`) and configure it
interactively:

```bash
./install.sh          # installs, then offers to run the setup wizard
prompt-coach setup     # choose which stores are active, LLM endpoint, nudge mode
```

Or run straight from the repo without installing:

```bash
uv sync
uv run prompt-coach discover             # list session stores with counts
uv run prompt-coach report --since 30d   # coaching report over recent history
uv run prompt-coach report --no-llm      # works with the GPU box switched off
uv run prompt-coach dash                 # terminal dashboard, no LLM needed
uv run prompt-coach query "what did I work on last week?"
uv run prompt-coach import chatgpt-export.zip   # ChatGPT official data export
```

## Commands

| Command | Description |
|---------|-------------|
| `setup` | Interactive wizard: choose which stores are active, LLM endpoint/model, nudge mode, model-fit mode, and an optional per-directory nudge override. Writes `config.toml`, then offers to run `report` or `dash`. |
| `discover` | Find available session stores (Hermes, Claude Code, Copilot, Codex CLI) |
| `report` | Coaching report: metrics, rubric scorecard, insights (`--since`, `--sample`, `--no-llm`, `--out`) |
| `dash` | Terminal dashboard: volume sparklines, segments, scorecard, project-doc quality (`--plain` for non-TTY, `--no-sync` to skip the store sync and render from cache as-is) |
| `stats` | Quick metrics table, no LLM needed |
| `query` | Natural-language question over your prompt history, with citations |
| `nudge` | Hook target for Claude Code (`UserPromptSubmit` + `Stop`) and Hermes (`pre_llm_call`). On Claude Code, default `coach` mode blocks weak prompts with an LLM-rewritten suggestion (degrades to a one-line tip if the LLM is unreachable), once per session; `always` mode blocks and rewrites every prompt; `off` disables it. On Hermes, `pre_llm_call` can only inject context, not block, so it's always tip-only regardless of mode (`always` collapses to `coach`'s behavior there). Set via `PROMPT_COACH_NUDGE_MODE` or `[nudge] mode` in config.toml. Not meant to be run by hand. |
| `import` | Import a ChatGPT export (ZIP/JSON) or simple JSON sessions; format auto-detected |
| `nudge-consume` | Internal target for the `/coach-accept` / `/coach-original` slash commands below -- pops the pending rewrite for a session and prints one side of it. Not meant to be run by hand. |
| `cache sync/info/clear` | Manage the local analysis cache |

## Claude Code slash commands

`~/.claude/commands/prompt-coach.md` (global, any project) runs
`/prompt-coach report --since 7d`, `/prompt-coach dash --plain`, etc. -- any
`prompt-coach` subcommand, passed straight through. Not part of this repo
(it's Claude Code user config), but worth knowing it exists.

`~/.claude/commands/coach-accept.md` and `~/.claude/commands/coach-original.md`
are the closest thing Claude Code's hook contract allows to a button on a
`nudge` block: `UserPromptSubmit` can only `block` (text reason) or
`continue`, there's no interactive-choice mechanism, so a real accept/reject
button isn't possible. Instead, when `nudge` blocks a prompt with a rewrite
it also stashes `{original, rewritten}` keyed by session ID
(`~/.cache/prompt-coach/nudge_pending.json`). `/coach-accept` resends the
rewrite; `/coach-original` resends your prompt untouched; both mark the
session to skip the nudge gate once, so the resend isn't immediately held
back again. Also global user config, not part of this repo.

## Wiring `nudge` into Hermes

Hermes's own hooks docs describe `pre_llm_call` as the direct equivalent of
Claude Code's `UserPromptSubmit`, and its shell hooks speak the same JSON
wire protocol. Add to `~/.hermes/config.yaml`:

```yaml
hooks:
  pre_llm_call:
    - command: "prompt-coach nudge"
      timeout: 5
```

Hermes prompts for one-time consent the first time the hook actually fires
(persisted to `~/.hermes/shell-hooks-allowlist.json`); non-interactive runs
(gateway, cron) need `--accept-hooks`, `HERMES_ACCEPT_HOOKS=1`, or
`hooks_auto_accept: true` set first, or the hook silently stays unregistered.
Unlike Claude Code, `pre_llm_call` can only inject context, never block, so
this path is always the deterministic tip (never an LLM-rewrite-and-block) --
see the `nudge` row above.

## How it works

1. **Sync**: prompts stream from each store into a local cache DB
   (`~/.cache/prompt-coach/`), incrementally; source stores are never written.
2. **Segment**: hand-typed prompts and machine-generated task specs (from
   `hermes -z` orchestration) are scored separately; the machine column audits
   your pipeline's prompt quality, the human column coaches you.
3. **Analyse**: deterministic style metrics and structural rubric checks run
   over everything; a local LLM judges the judgement rules and detects
   patterns on a stratified sample, with every call cached.
4. **Report**: one markdown briefing. Delete `~/.cache/prompt-coach/` to erase
   every derived artifact.

## Privacy

The LLM client refuses any endpoint that is not localhost or a private
(RFC1918) address unless you explicitly set `allow_remote = true` in config.
No cloud API keys, no telemetry, no egress.

## Configuration

`~/.config/prompt-coach/config.toml` (all optional; `prompt-coach setup` writes
this for you), or env vars:

- `PROMPT_COACH_API_BASE` - local LLM endpoint (default `http://192.168.1.123:11434/v1`)
- `PROMPT_COACH_MODEL` - analysis model (default `qwen3-coder-30b:latest`, 32k num_ctx baked in)
- `PROMPT_COACH_HERMES_DB`, `PROMPT_COACH_CLAUDE_PROJECTS`, `PROMPT_COACH_COPILOT_DIR`,
  `PROMPT_COACH_CODEX_DIR`, `PROMPT_COACH_CACHE_DIR`
- `PROMPT_COACH_ENABLED_STORES` - comma-separated store list (default: all four
  live stores). Opt-in, not opt-out: a store present on disk is only used if
  named here. Equivalent to `[stores] enabled = [...]` in config.toml.
- `[nudge.dir_overrides]` in config.toml only (a mapping has no clean env-var
  form) - per-directory nudge mode, e.g. `"/home/x/scratch" = "off"`. The
  longest matching path prefix wins; falls back to the global `nudge.mode`.

## Docs

- `BLUEPRINT.md` - full spec and build plan
- `AGENTS.md` - canonical guidance for AI coding agents
- `STATUS.md` / `DECISIONS.md` - progress and decision history
