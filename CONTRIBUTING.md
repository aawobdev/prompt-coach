# Contributing to prompt-coach

Thanks for helping. Bug reports, new source readers and heuristic fixes are
all welcome.

Fair warning: the codebase is very vibe-coded. Most of it was written by AI
coding agents, so you will find long comments, over-documented decisions and
some patterns a human would not have chosen. AI-assisted contributions are
welcome on the same terms as any other: tests pass, and you have read and
understood what you are submitting.

## Setup

```bash
uv sync --extra dev      # bare `uv sync` omits pytest/ruff/black
uv run pytest
uv run ruff check .
uv run black --check .
```

CI runs those three plus a check that the project docs contain no em dashes
(house style), and validates the Claude Code plugin manifests.

## Ground rules

- **Never write to a source store.** Readers open history read-only; all
  derived state lives in `~/.cache/prompt-coach/`.
- **No real prompts in fixtures.** Prompt history is personal. Write
  synthetic test prompts, and scrub names, paths and hostnames from anything
  you base them on.
- **Check heuristics against real history.** A scoring or regex change can
  pass every unit test and still misfire on real prompts (pasted logs,
  harness wrapper text). Run an old-vs-new comparison over your own history
  with `prompt-coach report --no-llm` and describe the result in the PR,
  without pasting the prompts themselves.
- **New source readers** live in `src/prompt_coach/stores/`. Record the format
  facts you verified (paths, which lines are human-typed) in `AGENTS.md`.
- Conventions (layout, tooling, decision log) are in [AGENTS.md](AGENTS.md).
  Add a dated entry to `DECISIONS.md` for any behaviour change.

## Pull requests

Keep them focused, include tests, and say how you verified the change.
