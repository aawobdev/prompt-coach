# Project Status - prompt-coach
Last updated: 2026-07-20 by Architect (Claude, Fable 5): phase 2 scoped and
starting (Copilot store, ChatGPT export import, dash); phase 1 complete

## Phase 2 task status

| Task | Description | Status | Notes |
|------|-------------|--------|-------|
| T35 | Copilot Chat store + tests | pending | format verified against live files |
| T36 | ChatGPT export store + tests | pending | format UNVERIFIED (no real export yet) |
| T37 | dash rendering module + tests | pending | rich, deterministic only |
| T38 | CLI wiring (dash, copilot, import auto-detect) | pending | |
| T39 | Phase-2 docs | complete | blueprint section 16, four DECISIONS entries |
| T40 | Phase-2 gate | pending | |

llm-api integration: skipped this phase by decision (see DECISIONS.md).

## Phase summary

| Phase | Role | Status | Notes |
|-------|------|--------|-------|
| Interview + inspection | Architect | complete | Live-system inspection corrected the v1 store facts |
| Blueprint v2 | Architect | complete | BLUEPRINT.md rewritten to blueprint-orchestration structure |
| Docs (T1-T4) | Architect | complete | |
| Build (T5-T33) | Developer/Tester | complete | 99 tests, ruff + black clean |
| Gate (T34) | Tester | complete | Live smoke on real corpora; see notes below |

## Task status

All tasks T1-T34 complete. Per-task commits are in git history (one commit per
task or small task group, message-prefixed T<n>).

## Gate results (2026-07-20, live corpora)

- Test suite: 99 passed. ruff and black clean. No em dashes in any doc.
- `discover`: hermes available (62 sessions, 281 prompts), claude-code
  available (108 main transcripts).
- First full sync: 7.6 seconds, 1,632 prompts (target was under 5 minutes).
- Incremental resync: 0.9 seconds (target under 30 seconds).
- `report --no-llm`: 1.8 seconds (target under 60 seconds); degraded banner
  renders, deterministic rubric rows and N/A coverage visible.
- `query`: FTS retrieval with snippet fallback works against real data.
- Live-model report (rubric LLM judge + patterns): NOT yet smoke-tested; the
  desktop Ollama box was off for the entire session. Run
  `uv run prompt-coach report --since 7d --sample 20` when it is on. Payload
  sizes are conservative, but the model's effective num_ctx is unverified.

## Findings recorded during the gate

- Claude Code tree holds 108 main transcripts; the other 536 JSONL files are
  subagent transcripts nested under `<session>/subagents/`, excluded by
  design (machine traffic). Docs corrected from the earlier 644 figure.
- ~30% of hand-typed prompts are micro-replies ("1", "y"); now excluded from
  rubric/pattern scoring, kept in metrics (see DECISIONS.md).
- The machine-prompt classifier is conservative: it catches `TASK:`-style
  specs (7 in the current corpus) but not, for example, cron-generated
  curator prompts ("You are curating..."). Acceptable for phase 1; refine if
  the machine segment matters more later.

## Blockers

None.

## Pending decisions

- Desktop Ollama num_ctx unverified (server off all session): batch sizes are
  conservative; check before tuning pattern batch defaults.
