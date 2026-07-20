# prompt-coach

**Your personal prompting analyst. Runs locally. Zero data export.**

A CLI tool that reads your own prompt history from the session stores already
on your machine (Hermes `state.db`, Claude Code transcripts, Copilot Chat
sessions, ChatGPT data exports), analyses your prompting style, and produces a
coaching report: style metrics, scores against the prompting-standards rubric,
and LLM-detected patterns. Prompt content never leaves your machine; analysis
runs on a local Ollama model, and the tool still works (deterministic analysis
only) when no model is reachable.

## Quick start

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
| `discover` | Find available session stores (Hermes, Claude Code, Copilot) |
| `report` | Coaching report: metrics, rubric scorecard, insights (`--since`, `--sample`, `--no-llm`, `--out`) |
| `dash` | Terminal dashboard: volume sparklines, segments, scorecard (`--plain` for non-TTY) |
| `stats` | Quick metrics table, no LLM needed |
| `query` | Natural-language question over your prompt history, with citations |
| `import` | Import a ChatGPT export (ZIP/JSON) or simple JSON sessions; format auto-detected |
| `cache sync/info/clear` | Manage the local analysis cache |

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

`~/.config/prompt-coach/config.toml` (all optional), or env vars:

- `PROMPT_COACH_API_BASE` - local LLM endpoint (default `http://192.168.1.123:11434/v1`)
- `PROMPT_COACH_MODEL` - analysis model (default `qwen3-coder:30b`)
- `PROMPT_COACH_HERMES_DB`, `PROMPT_COACH_CLAUDE_PROJECTS`, `PROMPT_COACH_CACHE_DIR`

## Docs

- `BLUEPRINT.md` - full spec and build plan
- `AGENTS.md` - canonical guidance for AI coding agents
- `STATUS.md` / `DECISIONS.md` - progress and decision history
