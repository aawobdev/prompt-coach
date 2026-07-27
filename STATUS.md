# Project Status - prompt-coach
Last updated: 2026-07-27 by Claude (Sonnet 5): CLI Visual Spec implemented
(dash/stats/report polish, D1-D9), sync progress bar added, and nudge
substantially upgraded (Stop hook, short-vague trigger, LLM block-and-
rewrite modes). 186 tests, up from 152. Not yet committed - see git status.

## CLI visual polish + nudge upgrades (2026-07-27)

Implemented the "CLI Visual Spec" design doc (Claude Design project,
decisions D1-D9) as a terminal-UX polish pass, not a feature build:
- `score_band()`/`score_label()` (models.py) - one good/fair/weak/n/a
  mapping shared by `dash`'s rich `Text` and `report`'s markdown.
- `stats` upgraded to a rich table matching `dash`'s conventions, no
  score-band colors (rates are behavior, not quality).
- `dash`: claude-code-first volume ordering, panels stack below 100 cols,
  narrow terminals collapse extra stores to "+N more", low-n rubric rows
  render dim, docs panel says "docs · clean" instead of vanishing
  silently, header shows store/stale counts, `--plain` drops sparklines
  (kept the column present to avoid a title-wrap bug found live).
- First-run and zero-prompts-in-window now exit 0 (were exit 1) across
  `dash`/`stats`/`report` - neither is an error.
- Per-store sync progress bar (spinner + N/4 + store name) on stderr for
  every sync call site, so a slow store (Copilot's /mnt/c reads) doesn't
  look like a hang. Falls back to silent when stderr isn't a terminal.
- `nudge` gained a `Stop` hook (fires after Claude's response, tail-reads
  the transcript since Stop's stdin carries no prompt text) alongside the
  existing `UserPromptSubmit` hook, sharing the once-per-session gate; a
  second calibrated trigger for short-but-vague prompts (real-corpus spike:
  26/1,739 human prompts, 1.5%, keywords everything/all of/whole/revamp -
  guessed synonyms never fired once); and three modes (`coach`/`always`/
  `off`, `NudgeConfig`) where the default `coach` mode now BLOCKS a weak
  prompt and offers an LLM-rewritten version in the reason text instead of
  just a passive tip, degrading gracefully when the LLM is unreachable.
  `~/.claude/settings.json`'s `UserPromptSubmit` timeout raised 5s -> 25s
  for the LLM round trip.

All of the above verified live (pty smoke tests, real subprocess CLI
invocations) as well as via the test suite. Full detail + rationale for
every judgment call is in DECISIONS.md (five entries, 2026-07-27).

## T43: Live nudge hook (2026-07-22)

A `UserPromptSubmit` Claude Code hook: `prompt-coach nudge` reads the hook's
stdin JSON (`prompt`, `session_id`) and prints a hook-response JSON. Fires a
one-line deterministic tip ("no output format or worked example in that
prompt") only when a prompt is long enough to be a real task ask (>=200
chars, same threshold as rubric.py's A4) and fails both A5/A6. Calibration
against the real corpus showed 77-97% of substantial human prompts already
fail those rules, so firing on every failure would nag on nearly every
message; instead it fires at most once per session (`nudge_state.json` in
the cache dir tracks nudged session IDs, capped at 500 entries). No LLM
call - hooks run synchronously and block prompt submission, so this must
stay near-instant. Never raises: a hook exception would block every prompt
you type, so malformed/missing stdin fields just produce `{}`.

Wired into `~/.claude/settings.json` (global, all projects):
`uv run --project ~/projects/prompt-coach prompt-coach nudge`, 5s timeout.
Verified: `jq -e` schema check passed; manual pipe-tests confirmed fire /
suppress-on-repeat / fire-again-in-new-session all behave correctly against
the real cache dir (test residue cleaned up after). Hook fires on your next
prompt submission per Claude Code's settings-file watch behaviour - if nudges
never appear, open `/hooks` once to force a reload.

New module `src/prompt_coach/nudge.py`, CLI command `prompt-coach nudge`,
15 new tests (`tests/test_nudge.py` + CLI tests).

## T42: Project documentation quality (2026-07-22)

Two spikes (docs-presence, then docs-quality) found: presence alone has no
signal (31/34 local project dirs already have CLAUDE.md/AGENTS.md/README),
but quality does vary once you look at the docs that exist (46 real files,
median 668 words, range 2-3290). Built `analysis/docs.py`: walks up from
each prompt's `cwd` to find the nearest project doc(s), scores size/
structure(headers+lists)/git-staleness, and flags `sparse` / `unstructured`
/ `stale`. Redirect stubs ("See AGENTS.md.") are detected and never flagged
- the spike found ~8 real projects use this pattern deliberately, and a
naive size check would have produced false "too sparse" flags on all of
them. Wired into `dash` as a new panel (flagged docs only, deterministic,
no prompt content - consistent with dash's existing privacy rule). Live
smoke against the real corpus: 46 docs found, 45 clean, 1 flagged (a
vendored README under `podcaster/old/_extracted/...`). 17 new tests
(`tests/test_docs.py`).

## Live-LLM smoke results (2026-07-21)

- `report --since 7d --sample 20` passed against live Ollama: rubric
  scorecard renders real human/machine scores, coaching insights and topic
  breakdown render. 66s wall clock.
- num_ctx finding: base tag `qwen3-coder:30b` has no num_ctx parameter and
  no OLLAMA_CONTEXT_LENGTH on the desktop, so it ran at the 4096 server
  default - below the ~8k-token pattern payloads (silent truncation).
  Default model changed to `qwen3-coder-30b:latest` (num_ctx 32768 baked
  in). Batch sizes can now be raised safely up to ~32k-token payloads if
  ever needed.
- Note: `--sample` sizes only the rubric sample; patterns take their own
  stratified sample (110 prompts here). By design.

## dash --no-sync (2026-07-21)

Added `--no-sync` to `prompt-coach dash`: skips `cache.sync(...)` and
renders straight from whatever is already in the cache. Avoids the ~17s
/mnt/c stat overhead on repeat runs during a session; first run (or after
new activity) still needs a plain `dash` or `cache sync` to pick up new
prompts. Two CLI tests added (117 total, up from 115).

## T41: Codex CLI store (2026-07-21, added post-gate)

Alistair flagged the actual gap is CLI agent harnesses, not the ChatGPT
web export ("more codex than chatgpt... this is more about cli agent
harnesses etc for now"). Found a real `~/.codex/sessions/` directory
(`/mnt/c` on this WSL box) with one genuine session (VS Code extension,
Oct 2025). Built `stores/codex_cli.py` against the live-verified format
(BLUEPRINT.md 16.4a): `session_meta`/`event_msg` rollout JSONL,
`user_message` events with `kind=plain`, IDE-context wrapper stripped down
to the actual request, `environment_context` kind dropped as boilerplate.
Wired into `default_stores`, config (`PROMPT_COACH_CODEX_DIR`), discover.
8 new store tests; live smoke against the real session extracted 4 clean
human prompts with no wrapper noise. 125 tests total (up from 117).
See DECISIONS.md for the ChatGPT-vs-Codex priority call.

## Next session pick-up list

0. **Uncommitted**: this whole session's work (CLI visual polish, progress
   bar, nudge Stop/short-vague/block-rewrite) is sitting in the working
   tree, not committed - see git status. Decide commit granularity (one
   bundled commit vs. split to match the DECISIONS.md entries) before
   starting new work.
1. Nudge's `coach`/`always` block-and-rewrite path was only smoke-tested
   with the LLM unreachable (sandboxed dev environment) or mocked
   (`_make_llm`/`rewrite_prompt` monkeypatched in tests) - never against a
   real reachable Ollama. Run it live once the desktop box is on and watch
   what an actual rewrite looks like; the `_REWRITE_SYSTEM` prompt and the
   `_block_reason()` wording are first-guess, not tuned against real output.
2. `always` mode's per-prompt LLM latency (bounded by `nudge.llm_timeout`,
   default 20s) hasn't been felt in practice - try it for real before
   deciding whether 20s is too generous/stingy, and whether `coach` mode's
   silent LLM-unreachable-once-per-session probe (an `httpx.get` on every
   trigger) is worth caching.
3. The short-vague nudge keyword list (`everything`/`all of`/`whole`/
   `revamp`) was calibrated on only 26 matching prompts - real signal, but
   a small sample; revisit if it misses obvious cases or fires on harmless
   ones in daily use.
4. Optional: ChatGPT importer verification if a real export ever shows up
   (`prompt-coach import <export.zip>`, clear UNVERIFIED markers here and
   in BLUEPRINT.md 16.2) - no longer the priority gap, see DECISIONS.md.
5. Optional: machine-prompt classifier misses cron-generated curator prompts
   ("You are curating..."); refine if the machine segment matters more.
6. Optional: Codex store only has one live session to validate against;
   revisit extraction rules (the `## My request for Codex:` wrapper strip,
   the `plain`/`environment_context` kind split) once more real usage
   accumulates, especially non-VS-Code (raw terminal) sessions.
7. Phase 3 candidates (blueprint section 15): trends over time, serve API,
   OpenWebUI store, weekly coaching report via Hermes cron.
8. Doc-quality thresholds (`_SPARSE_WORDS=150`, `_STALE_DAYS=90`) are
   first-guess; tune once there's a project you'd actually flag.
9. Local web UI for insights (raised 2026-07-22, not started): fills the
   `serve` stub (`cli.py`, currently "phase 2; not implemented yet") and
   the phase-3 "serve API" candidate above. Two options discussed:
   (a) static HTML export - `prompt-coach dash --html report.html`,
   reusing `dash.py`'s existing renderable data, zero new deps, no
   long-running process; (b) local Flask/FastAPI server bound to
   `127.0.0.1` only (same localhost-only rule as the LLM client) for live
   filtering/session drill-down, more moving parts. Leaning towards
   spiking (a) first since nothing here needs live updates - only build
   (b) if the static page turns out too limiting. No decision made yet.

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

None. (Ollama num_ctx question resolved 2026-07-21: see live-LLM smoke
results above and DECISIONS.md.)
