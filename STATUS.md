# Project Status - prompt-coach
Last updated: 2026-07-20 by Architect (Claude, Fable 5): blueprint v2 finalised,
build starting

## Phase summary

| Phase | Role | Status | Notes |
|-------|------|--------|-------|
| Interview + inspection | Architect | complete | Live-system inspection corrected the v1 store facts |
| Blueprint v2 | Architect | complete | BLUEPRINT.md rewritten to blueprint-orchestration structure |
| Docs (T1-T4) | Architect | in progress | |
| Build (T5-T33) | Developer/Tester | pending | Claude Code executes all tasks |
| Gate (T34) | Tester | pending | |

## Task status

| Task | Description | Status | Notes |
|------|-------------|--------|-------|
| T1 | BLUEPRINT.md rewrite | complete | verify gate passed |
| T2 | AGENTS.md corrections | complete | |
| T3 | STATUS.md created | complete | this file |
| T4 | DECISIONS.md seeded | complete | |
| T5 | pyproject prune (drop ORM/async deps) | pending | |
| T6 | models.py shared types | pending | |
| T7 | config.py loader | pending | |
| T8 | stores/base.py protocol + origin classifier | pending | |
| T9 | stores/hermes.py reader | pending | |
| T10 | Hermes store tests | pending | |
| T11 | stores/claude_code.py reader | pending | |
| T12 | Claude Code store tests | pending | |
| T13 | stores/json_import.py | pending | |
| T14 | sample_sessions.json fixture data | pending | |
| T15 | JSON store tests | pending | |
| T16 | cache.py (sync, dedupe, FTS, llm cache) | pending | |
| T17 | cache tests | pending | |
| T18 | llm/client.py with privacy guard | pending | |
| T19 | llm/prompts.py versioned templates | pending | |
| T20 | LLM client tests | pending | |
| T21 | analysis/metrics.py | pending | |
| T22 | metrics tests | pending | |
| T23 | analysis/rubric.py (A1-A13) | pending | |
| T24 | rubric tests | pending | |
| T25 | analysis/patterns.py map-reduce | pending | |
| T26 | patterns tests | pending | |
| T27 | report template | pending | |
| T28 | report/generator.py | pending | |
| T29 | report tests | pending | |
| T30 | query.py | pending | |
| T31 | cli.py rewrite | pending | |
| T32 | CLI tests | pending | |
| T33 | README.md update | pending | |
| T34 | Tester gate (suite, lint, live smoke) | pending | |

## Blockers

None.

## Pending decisions

- Desktop Ollama num_ctx unverified (server off during design): batch sizes
  set conservatively; check before tuning pattern batch defaults.
