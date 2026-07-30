# Project Status - prompt-coach
Last updated: 2026-07-30 by Claude (Sonnet 5): Claude Code slash command +
Hermes nudge equivalent shipped, two test gaps closed, and CI added. 243
tests, up from 231. Not yet committed - see git status.

## Claude Code slash command + Hermes nudge equivalent (2026-07-30)

DECISIONS.md has the full platform-by-platform research (Claude Code,
Hermes, Copilot, Codex all checked against current docs, not assumed).
Shipped:
- `~/.claude/commands/prompt-coach.md` (global, outside this repo):
  `/prompt-coach report --since 7d` etc. runs the real CLI and injects its
  output into context.
- `nudge.py`/`build_response` now also handles Hermes's `pre_llm_call`
  shell hook (`_hermes_tip_response`) - same calibrated triggers/once-
  per-session gate, but tip-only via `{"context": ...}` since Hermes's
  hook can't block the way Claude Code's UserPromptSubmit can. Same
  `prompt-coach nudge` command serves both platforms; wiring snippet for
  `~/.hermes/config.yaml` is in README.md.
- Copilot/Codex: no dedicated shortcut built - confirmed against the
  official VS Code docs that Copilot's prompt files can't do shell
  injection, and Codex isn't installed as a standalone CLI here. Both
  already work via "ask it directly in chat," just without a slash-command
  shortcut.
- **Bug found and fixed via live smoke test**: nudge's scope-tip text
  said "before Claude starts", hardcoded even though the hook now also
  fires for Hermes sessions running arbitrary models. Genericized.
- 240 tests (up from 231): tests/test_nudge.py `TestHermesPreLlmCall`
  added. ruff/black clean, no em dashes.
- Follow-up (same session): two gaps closed after asking "do we need more
  tests?" - tests/test_import_hygiene.py (subprocess-based regression
  guard: fails if `openai` ever gets eagerly imported by nudge.py/cli.py
  again, protecting the 2026-07-29 latency fix) and a `setup` wizard test
  for entering a public LLM URL (RemoteEndpointRefused was already caught
  in code, just untested). 243 tests total.
- **CI added** (`.github/workflows/ci.yml`, new): pytest, ruff, black, and
  an em-dash check on every push/PR - none of this ran automatically
  before. Rehearsing it locally from a clean `.venv` caught a real bug
  before it ever reached GitHub: bare `uv sync` does not install the `dev`
  optional-dependencies group (pytest/ruff/black), so the workflow
  originally failed at the test step. Fixed to `uv sync --extra dev`,
  and AGENTS.md's Commands section had the same bare `uv sync` mistake -
  fixed there too (README's Quick Start was fine, it never runs
  pytest/ruff/black). Confirmed passing end-to-end from a from-scratch
  `.venv` before considering it done, not just re-using the already-primed
  local environment.

## Setup wizard, install.sh, store enable/disable, per-directory nudge (2026-07-30)

BLUEPRINT.md 17.3 has the shape; DECISIONS.md (2026-07-29 "Setup
experience" entry + 2026-07-30 "per-directory nudge" entry) has the full
reasoning. Highlights:
- `install.sh` (repo root): `uv tool install --force .` + offers to run
  `prompt-coach setup`. Not a compiled binary - deferred until/unless
  nudge-hook latency (fixed below, but not to zero) is still a problem.
- `prompt-coach setup`: per-store enable/disable with live discover()
  counts shown, LLM endpoint/model with a live reachability check, nudge/
  model-fit mode, an optional per-directory nudge override for the
  directory setup is run from, then offers to run `report`/`dash`
  immediately. Writes `~/.config/prompt-coach/config.toml` via a new
  hand-formatted TOML writer (no new dependency - tomllib is read-only).
- `StoresConfig.enabled`: opt-in list, default all four live stores
  (Hermes/Claude Code/Copilot/Codex), so a store present on disk is only
  used if named. `default_stores()` in cli.py filters on it.
- `NudgeConfig.dir_overrides`: config.toml-only path->mode mapping.
  Checked Claude Code's hooks docs live before building anything: hooks
  registered at different scopes (global/project settings.json) merge
  rather than override, so there's no way to exclude one project from a
  globally-registered hook via Claude Code's own settings - per-directory
  control has to live in prompt-coach itself, keyed off the hook
  payload's `cwd` (confirmed present on both UserPromptSubmit and Stop).
  `nudge.py`'s `_resolve_mode()` picks the longest matching path prefix.
- Verified live: full `setup` run against the real corpus (65 Hermes
  sessions, 89 Claude Code, 167 Copilot, 1 Codex) correctly excluded a
  disabled store from a subsequent `discover`; LLM reachability check
  correctly reported "reachable now" against the live desktop Ollama.
- 231 tests (up from 213): tests/test_config.py new; test_nudge.py and
  test_cli.py extended (interactive wizard flows via CliRunner `input=`).
  ruff/black clean, no em dashes.

## Nudge hook latency fixed (2026-07-29)

Alistair had disabled the nudge hook (`hooks: {}` in settings.json),
suspecting delay. Confirmed live: `cli.py` and `nudge.py` eagerly imported
the entire CLI + the `openai` SDK on every single prompt submission
regardless of mode, dominated by `openai`'s own type surface (~700-900ms).
Fixed via lazy per-command imports; mode=off/non-triggering dropped from
~1.3-1.5s to ~0.4-0.5s per prompt (roughly 3x). Full numbers and root
cause in DECISIONS.md. **Hook is still not re-registered in
`~/.claude/settings.json`** - that's a decision for Alistair now that the
cost is measured, not done automatically since it touches live Claude Code
config.

## Model fit (2026-07-29)

New analysis dimension: flags prompts where what they demanded and the
model that handled them look mismatched. Deterministic only, no LLM call.
See DECISIONS.md (two entries, 2026-07-29) for the full scope decision and
build detail. Highlights:
- `Prompt.model` captured in all four live stores, per-turn where the
  store supports it (Claude Code, Codex CLI), per-request (Copilot), or
  session-level only (Hermes, no per-message model column exists).
- `analysis/model_fit.py`: `classify_model_tier` (Claude's public haiku/
  sonnet/opus ladder + param-count regex for local models; unclassified
  rather than guessed for gpt-5-codex, claude-fable-5, copilot/auto),
  `estimate_demand_tier` (char-length buckets, first-guess thresholds),
  `detect_mismatches` (off/descriptive/prescriptive modes, `NudgeConfig`-
  style config at `ModelFitConfig`).
- New dash panel and report section, both privacy-safe (no prompt content,
  same rule as the docs-quality panel).
- **Known gap, found live**: the cache dedupes on insert, so prompts
  already cached before this change keep `model=NULL` forever; only
  freshly-synced prompts (or a `cache clear` + resync) populate it. Real
  corpus currently shows 16/916 eligible prompts classifiable until that
  happens. Not a bug, just a one-time backfill the user needs to trigger.
- 213 tests (up from 186): new tests/test_model_fit.py plus store/cache/
  nudge fixture updates for the new field. ruff and black clean.

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
   bar, nudge Stop/short-vague/block-rewrite, model fit, the nudge latency
   fix, the setup wizard/install.sh/store enable-disable/per-directory
   nudge, and now the Claude Code slash command + Hermes nudge equivalent)
   is sitting in the working tree, not committed - see git status. Decide
   commit granularity before starting new work.
0e. **Hermes hook not actually wired**: the `pre_llm_call` -> `prompt-coach
   nudge` snippet is documented in README.md but not added to the real
   `~/.hermes/config.yaml` - same reasoning as not auto-enabling the
   Claude Code hook, this touches live config and Alistair should opt in.
   Also untested against a real `hermes chat` session (only smoke-tested
   with a synthetic stdin payload matching the documented wire protocol).
0c. **install.sh not yet run for real**: syntax-checked and its individual
   pieces (`uv tool install`, PATH check) verified logically, but the
   script end-to-end was not actually run against the user's real
   `~/.local/bin` - do that once, and confirm the `prompt-coach setup`
   hand-off at the end works from a truly fresh shell.
0d. **Nudge hook still not re-registered** in `~/.claude/settings.json`
   (disabled since before the 2026-07-29 latency fix) - re-enabling it is
   the natural next step now that `setup` can configure mode/dir_overrides
   without hand-editing config.toml, but that's still Alistair's call.
0a. **Model fit backfill**: run `prompt-coach cache clear` once (then a
   normal sync) to get real coverage numbers - historical cached prompts
   don't retroactively gain a `model` value, so today's live smoke only
   showed 16/916 classifiable. Cache is fully disposable/re-derivable per
   the project's own retention story, but clearing it wasn't done
   automatically since it touches real local data.
0b. Model fit's thresholds (`_LOW_DEMAND_CHARS=200`, `_HIGH_DEMAND_CHARS=
   1200`, `_SMALL_MAX_B=9`, `_MEDIUM_MAX_B=39`) are first-guess, not
   calibrated against labelled data - revisit once the backfill above
   gives a real sample of findings to look at (same posture as docs.py's
   thresholds when they shipped).
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
