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
| `nudge` | Hook target for Claude Code (`UserPromptSubmit` + `Stop`) and Hermes (`pre_llm_call`). On Claude Code, default `coach` mode blocks weak prompts with an LLM-rewritten suggestion (degrades to a one-line tip if the LLM is unreachable), once per session; `always` mode blocks and rewrites every prompt (a bare `y`/`n`/`ok` answer is let through); `auto` never blocks: it asks the session model to refine the prompt itself, or with `[nudge] auto_rewrite = true` sends the LLM's rewrite straight through (falling back to self-refine); `off` disables it. After a block, reply `y` to send the rewrite or `n` to send your original. On Hermes, `pre_llm_call` can only inject context, not block, so it's always tip-only regardless of mode (`always` collapses to `coach`'s behavior there). Set via `PROMPT_COACH_NUDGE_MODE` or `[nudge] mode` in config.toml. Not meant to be run by hand. |
| `import` | Import a ChatGPT export (ZIP/JSON) or simple JSON sessions; format auto-detected |
| `nudge-consume` | Internal target for the `/prompt-coach:accept` / `/prompt-coach:original` plugin skills below -- pops the pending rewrite for a session and prints one side of it. Not meant to be run by hand. |
| `doctor` | Read-only report: is the `claude` CLI on PATH, is the plugin installed, are the legacy hooks/commands it replaces still present (`--json` for scripting). |
| `migrate-legacy` | Installs the plugin (via `claude plugin marketplace add`/`install`) and/or removes the legacy hooks/commands, whichever is needed. Shows the plan and asks to confirm; backs up everything it touches first. `--repo` overrides auto-detection, `--dry-run` previews only, `--yes` skips confirmation. |
| `cache sync/info/clear` | Manage the local analysis cache |

## Claude Code plugin

The hooks and slash commands ship as a Claude Code plugin in `plugin/`
(marketplace manifest at `.claude-plugin/marketplace.json`). It is a thin
wrapper: the hooks and skills call the installed `prompt-coach` CLI through
`plugin/scripts/pc`, a fail-open launcher that looks for the CLI on `PATH`,
then `~/.local/bin` (where `uv tool install` puts it). If the CLI is
missing it warns once and lets every prompt through.

Install (the CLI first, via `./install.sh`):

```bash
claude plugin marketplace add aawobdev/prompt-coach   # or a local path to this repo
claude plugin install prompt-coach@prompt-coach
```

What it registers:

| Piece | What it does |
|---|---|
| `SessionStart` hook (5s, new sessions only) | One line when a session starts: prompts since your last nudge and this week's most common gap, e.g. "39 prompts since your last nudge. This week's most common gap: no output format or example (13 of 15 nudges)". Shown to you, not added to Claude's context (no tokens). Silent on resume, with no history yet, or when nudge mode is `off`. |
| `UserPromptSubmit` hook (25s) | Runs `prompt-coach nudge`: scores the prompt, blocks with a rewrite (coach/always) or improves it in place (auto: the refine directive, or the LLM's rewrite with `auto_rewrite`). Also handles the `y`/`n` reply to a block. |
| `Stop` hook (10s) | Runs `prompt-coach nudge` for the hint-mode tip. |
| `/prompt-coach:accept` | Resends the rewrite `nudge` held back. |
| `/prompt-coach:original` | Resends your original prompt untouched. |
| `/prompt-coach:run <cmd>` | Runs any `prompt-coach` subcommand (`report --since 7d`, `dash --plain`, ...). |

`UserPromptSubmit` can only `block` (text reason) or add context, so there
is no real accept/reject button, and the hook can't replace your prompt
text. Instead, when `nudge` blocks a prompt with a rewrite it stashes
`{original, rewritten}` keyed by session ID
(`~/.cache/prompt-coach/nudge_pending.json`). Then:

| You send | What happens |
|---|---|
| `y` | Goes through; the rewrite rides along as context and Claude answers it |
| `n` | Same, with your original prompt |
| `/prompt-coach:accept` / `:original` | Same result via the skills; they also mark the session to skip the nudge gate once |
| anything else | Handled normally; the pending rewrite is dropped, so a later `y` can't resurrect it |

The desktop app never shows the text that was actually sent, so every path
tells Claude to open its reply by quoting it (`Sent (prompt-coach rewrite):`
or `Sent (your original prompt):`). The three skills are
user-only (`disable-model-invocation`), so Claude cannot trigger them.

Plugin skills are always namespaced `/<plugin>:<skill>`, which is why the
old short names `/coach-accept` and `/coach-original` became
`/prompt-coach:accept` and `/prompt-coach:original`.

**Migrating from the manual setup:** Claude Code does not de-duplicate a
plugin's hooks against the same hooks in `~/.claude/settings.json`, so
running both at once nudges every prompt twice. Run `prompt-coach
migrate-legacy` (or say yes when `prompt-coach setup` offers it): it
installs the plugin if it isn't already, removes the two legacy
`prompt-coach nudge` hook entries and the three old
`~/.claude/commands/*.md` files, and backs up everything it touches first
(`<file>.bak-<timestamp>`, same convention either way). `prompt-coach
doctor` reports the current state without changing anything, including
whether both the plugin and the legacy hooks are active at once.

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
By default: no cloud API keys, no telemetry, no egress.

Opting in to a cloud rewrite model (e.g. DeepSeek via OpenRouter) means
triggered prompts, plus the project-doc excerpt and recent prompts used as
context, leave the machine. Keep the key out of config with `api_key_cmd`,
and prefer a provider account that enforces zero data retention.

## Configuration

`~/.config/prompt-coach/config.toml` (all optional; `prompt-coach setup` writes
this for you), or env vars:

- `PROMPT_COACH_API_BASE` - local LLM endpoint (default `http://192.168.1.123:11434/v1`)
- `PROMPT_COACH_MODEL` - analysis model (default `qwen3-coder-30b:latest`, 32k num_ctx baked in)
- `PROMPT_COACH_ALLOW_REMOTE` / `[llm] allow_remote` - allow a non-private endpoint (default false)
- `PROMPT_COACH_API_KEY_CMD` / `[llm] api_key_cmd` - shell command that prints the
  API key, run only when an LLM client is built, so the key never sits in config.
  `PROMPT_COACH_API_KEY` still wins for one-off runs.
- `PROMPT_COACH_REASONING` / `[llm] reasoning` - `false` asks an OpenRouter-style
  endpoint to skip reasoning (unset = send nothing). Needed for DeepSeek v4
  flash, which otherwise spends its whole budget reasoning and times out.
- `PROMPT_COACH_NUDGE_MODE` / `[nudge] mode` - `coach` (default), `auto`, `always`, `hint`, `off`
- `PROMPT_COACH_NUDGE_AUTO_REWRITE` / `[nudge] auto_rewrite` - in auto mode, send the
  LLM's rewrite straight through instead of the self-refine directive (default false)
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
