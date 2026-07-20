# Project Status - prompt-coach
Last updated: 2026-07-20 by Claude (Fable 5), daily wrap-up: phases 1 and 2
both complete and gated in a single day (blueprint v2 rewrite through dash)

## Next session pick-up list

1. Live-LLM smoke (needs desktop Ollama on):
   `uv run prompt-coach report --since 7d --sample 20` - verify rubric judge
   and patterns render; check the model's effective num_ctx before raising
   batch sizes.
2. ChatGPT importer verification: run `prompt-coach import <export.zip>`
   against a real export, then clear the UNVERIFIED markers here and in
   BLUEPRINT.md section 16.2.
3. Optional UX: sync throttle or --no-sync flag (dash pays ~17s of /mnt/c
   stat overhead per run).
4. Optional: machine-prompt classifier misses cron-generated curator prompts
   ("You are curating..."); refine if the machine segment matters more.
5. Phase 3 candidates (blueprint section 15): trends over time, serve API,
   OpenWebUI store, weekly coaching report via Hermes cron.

## Phase 2 task status

| Task | Description | Status | Notes |
|------|-------------|--------|-------|
| T35 | Copilot Chat store + tests | complete | format verified against live files |
| T36 | ChatGPT export store + tests | complete | format UNVERIFIED (no real export yet) |
| T37 | dash rendering module + tests | complete | rich, deterministic only |
| T38 | CLI wiring (dash, copilot, import auto-detect) | complete | |
| T39 | Phase-2 docs | complete | blueprint section 16, four DECISIONS entries |
| T40 | Phase-2 gate | complete | see gate results below |

llm-api integration: skipped this phase by decision (see DECISIONS.md).

## Phase 2 gate results (2026-07-20, live corpora)

- Suite: 143 tests passed; ruff and black clean; em-dash gates pass.
- Live Copilot sync over /mnt/c: 24s first pass, 23 prompts extracted from
  155 session files (many sessions hold no requests).
- `dash --plain` renders on real data: per-store sparklines (claude-code,
  copilot, hermes), human/machine split, deterministic scorecard. 17.5s
  wall clock, almost all of it /mnt/c stat overhead during the implicit
  sync. Known limitation: WSL 9P filesystem I/O; consider a sync throttle
  or --no-sync flag if it grates.
- ChatGPT importer: synthetic-fixture tested only; run
  `prompt-coach import <export.zip>` against a real export to verify, then
  update the UNVERIFIED marker here.

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
