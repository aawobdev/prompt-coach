# Project Status - prompt-coach
Last updated: 2026-09-24 by Claude (Opus 5.5): second wrap-up. Plugin
phases 1-3, the scoring/paste fixes and the reinstall fix are merged and
live on `dev`; see "Live state and open items" below. 433 tests.

## Live state and open items (2026-09-24, second wrap-up)

- **Live on `dev`**: `main` at `77e408c` (PRs #2-#7 merged). Installed CLI
  rebuilt from `9b0e0ad` (phase 3) with a forced refresh; #7 changed only
  build settings and tests, so no reinstall was needed after it, and from
  #7 on a plain `uv tool install --force .` rebuilds correctly. Plugin
  enabled from the `/home/alistair/projects/prompt-coach` directory
  marketplace, so hook/skill changes load in place at the next session.
- **Verified on the installed build**: `discover` reads Claude Code
  sessions again; a real `claude -p` session (temp copy of the cache) showed
  the SessionStart summary and the model confirmed it wasn't in its
  context; the paste tip and scoring fixes respond through the plugin hook.
- **Legacy setup backups** (from `migrate-legacy`):
  `~/.claude/settings.json.bak-20260924-172300` (plus a manual
  `.bak-manual-20260924-172259`) and `~/.claude/commands/*.md.bak-20260924-172300`.
- **Next**: optional plugin phase 4 (status-line offer in `setup`,
  `claude plugin validate` in CI).
- **Known limits** (DECISIONS.md 2026-09-24): pasted documents (form
  fields, contracts, help text) still read as sections about half the time;
  any percentage in a bullet counts as a constraint.
- **Other remote branches** not from this work, left alone:
  `claude/auto-prompt-improvement-7b51b7`,
  `claude/hook-prompt-interception-buttons-1a6cf7`, `claude/next-steps-a0d86d`,
  `al/add-claude-md-stub` (PR #1, closed unmerged).

## Reinstall no longer reuses stale builds (2026-09-24)

`uv tool install --force .` (install.sh and the pull-and-reinstall deploy
step) reused uv's cached wheel whenever pyproject.toml hadn't changed, so a
merge could deploy the *previous* code with no error. Caught live: after
merging phase 3 the SessionStart hook returned nothing until a forced
rebuild. `[tool.uv] cache-keys` (pyproject.toml) now keys the build on
`src/**/*.py`, `src/**/*.j2` and the git commit.
`tests/test_install_freshness.py` reproduces the deploy step (install,
code-only commit, reinstall) and fails without the setting (mutation-checked).
Phase 3 verified live on `dev` after the rebuild: a real session showed the
summary, and the model confirmed it wasn't in its context.

## Plugin phase 3: SessionStart summary (2026-09-24)

DECISIONS.md (2026-09-24, phase 3) has the reasoning. Shipped on branch
`claude/plugin-phase3-session-start`:
- `session_summary.py`: prompts since your last nudge, plus the most common
  nudge trigger in the last 7 days with a one-line fix. Real output:
  "39 prompts since your last nudge. This week's most common gap: no output
  format or example (13 of 15 nudges) -- name the shape you want back."
  Reads streaks.json and the audit log only; counts, never prompt content.
- `hooks.json`: `SessionStart`, matcher `startup`, 5s timeout, through the
  same fail-safe `pc nudge` entry point (dispatch in `build_response`).
- Returned as a JSON `systemMessage` only: shown to the user, not added to
  the model's context. Verified live with `claude -p --plugin-dir` +
  stream-json: the hook fired with real data and the model, asked whether
  its context contained the summary, answered "CLEAN". `--continue`
  (resume) fired no hook. Cost ~0.15s, same as bare CLI startup.
- Silent on resume/clear/compact/fork, with no history, and in `off` mode
  (including per-directory `off` overrides).
- Fixed a pre-existing `streaks.py` bug the new tests caught: a fresh cache
  got a shallow copy of the defaults, so fresh caches shared one module-level
  history list and it leaked between caches within one process.

## Scoring fixes + paste detection (2026-09-24)

Started as a review of uncommitted WIP found on `main` (a `has_sections`
bonus and a follow-up-message exemption), which turned up 8 bugs, then a
real-corpus test pass that found 2 more. DECISIONS.md (2026-09-24) has the
reasoning. Shipped on branch `fix/has-sections-follow-up-scoring` (PR #4):
- **Claude Code store was reading zero prompts.** `promptSource` is no
  longer `typed` in any real transcript (0 of 277,872 lines); accepting
  `sdk` recovers 4,361. `is_harness_wrapped` (stores/base.py, now shared
  with nudge.py) rejects the 143 wrapper lines that came with it.
- Signal regexes: `_STRUCTURED` no longer matches bare table/columns or
  "list of" without an output verb; implicit constraints need a real bound;
  `e.g.`/`example:` actually match now (dead on `main`); `has_sections`
  uses general labels, not a 10-word list. The follow-up exemption was
  removed, not patched: it can't be judged from an isolated string.
- **Paste detection**: every structure signal reads `authored_text()`
  (the prompt minus fences, machine-output lines and key-value dumps). A
  fence is an example only when cued. `is_bare_paste()` adds a -15 score
  penalty and its own nudge tip, which never rewrites-and-blocks.
- Verified on the real corpus (3,722 prompts, all three versions side by
  side): sections bonus 1.64% -> 0.70%, 18 bare pastes flagged and all 18
  genuine, A4 unchanged. Every fix mutation-checked.
- Known limits: pasted *documents* (form fields, contracts, help text)
  still read as sections about half the time; percentages in any bullet
  count as constraints (2 of 3,722 prompts).

## Claude Code plugin, phase 2: doctor + migrate-legacy (2026-09-24)

Closes the "phase 1 shipped the plugin but nothing installs or migrates it"
gap noted at the end of phase 1 below. DECISIONS.md (2026-09-24) has the
full reasoning. Shipped on branch `claude/prompt-coach-claude-features-4d502e`:
- `plugin.py`: `find_repo_root()` reads `uv`'s own tool receipt
  (`uv tool dir`/`<name>/uv-receipt.toml`) to locate this repo without a new
  marker file -- `uv tool install .` copies the package into an isolated
  venv, so nothing under the installed CLI's own path points back at the
  checkout. `plan_migration()` reads `~/.claude/settings.json` and
  `~/.claude/commands/` and returns exactly what would change, touching
  nothing. `install_plugin()` shells out to the real `claude` CLI
  (`plugin marketplace add` + `plugin install ... -y`) rather than
  hand-writing `enabledPlugins`/`extraKnownMarketplaces` -- both calls are
  idempotent, verified live against a sandboxed `~/.claude`.
- `prompt-coach doctor` (`--json` too): read-only report on claude-CLI
  presence, plugin-enabled state, legacy hooks/commands found, and a
  flagged double-fire risk when both the plugin and the legacy hooks are
  active at once.
- `prompt-coach migrate-legacy` (`--repo`, `--dry-run`, `--yes`): shows the
  plan, confirms, then installs (if needed) and removes the legacy
  hooks/commands (if any) -- backing up `settings.json` and every legacy
  command file first (`<file>.bak-<timestamp>`, same convention already
  used by hand for `CLAUDE.md` on this machine). Install failure leaves
  the legacy hooks untouched (never ends up with neither working).
- `setup` now offers the same flow near the end, silently, only when
  there's something to do.
- Found and fixed live, not by the mocked-install unit tests: applying a
  hook-removal plan captured *before* `install_plugin()` ran clobbered the
  real `claude` CLI's own settings.json write (`enabledPlugins`,
  `extraKnownMarketplaces`) the moment later. `_execute_migration` now
  re-reads the plan fresh right before writing; the regression test's
  install mock now writes to the file the way the real CLI does, to catch
  this class of bug going forward.
- `tests/test_plugin_migrate.py` (37 tests, unit-level, no real `claude`)
  and 20 new tests in `test_cli.py` (doctor/migrate-legacy/setup-offer,
  CliRunner level, `claude` always mocked). Verified live end-to-end
  twice more: a sandboxed `~/.claude` with the real `claude` binary
  (Claude Code's own bundled CLI, `$CLAUDE_CODE_EXECPATH` -- the copy at
  `~/.hermes/node/.../bin/claude.exe` turned out to be a broken 500-byte
  postinstall stub as of 2026-09-23, unrelated to this repo, flagged
  separately) both before and after the fix above.
- Not done: nothing calls `install_plugin`/`migrate-legacy` automatically
  from `install.sh` itself -- `setup` is still a separate, opt-in step.

## Claude Code plugin, phase 1 (2026-09-20)

Closes the "fresh installs are inert" gap: `install.sh` and `setup` never
registered the hooks or slash commands, which existed only as hand edits in
`~/.claude/settings.json` and `~/.claude/commands/`. DECISIONS.md
(2026-09-20) has the reasoning. Shipped on branch
`claude/prompt-coach-claude-features-4d502e`:
- `plugin/` (hooks.json, fail-open `scripts/pc` launcher, skills `accept`,
  `original`, `run`) and `.claude-plugin/marketplace.json`; both pass
  `claude plugin validate --strict`.
- Block text now says `/prompt-coach:accept` / `/prompt-coach:original`
  (`ACCEPT_COMMAND` / `ORIGINAL_COMMAND` in nudge.py).
- `tests/test_plugin.py`: manifest/version sync, hooks shape, launcher
  behaviour (fail-open, thin PATH, once-only warning), skill-to-CLI
  consistency. Mutation-checked (lost exec bit, renamed CLI target).
- Verified live with the real plugin only (`--setting-sources project,local`
  to exclude the old hooks): audit interventions 1 -> 2 (hook fired),
  `/prompt-coach:accept` and `/prompt-coach:run score` ran through the
  launcher. Not proven: the model following auto mode's "Refined:"
  directive (haiku asked a clarifying question instead), and PATH in the
  desktop app's hook environment (the launcher has a `~/.local/bin` fallback
  for it).
- NOT yet done: the live `~/.claude/settings.json` still has the old hooks,
  and the plugin is not installed there (installing both double-fires).

### Next (phases 2-4 of the plan)
2. `prompt-coach doctor`, a `setup` step that installs the plugin, and a
   backed-up, confirmed migration that removes the legacy hooks and
   commands. 3. `SessionStart` hook showing streak/top weakness as a
   user-visible `systemMessage`. 4. Optional: status-line offer in `setup`
   (plugins cannot ship a `statusLine`), `claude plugin validate` in CI.

## Web dashboard: `dash --html` + `serve` (2026-08-17)

Closed pick-up item 9 (local web UI for insights) and the phase-3
"serve API" candidate, taking option (a) from that item -- reuse the
existing terminal dashboard's renderable rather than build a separate
web stack. DECISIONS.md (2026-08-17) has the full reasoning. Shipped:
- `dash --html <path>`: renders the dashboard into a fixed-width color
  console (rich `record=True`, backed by an in-memory buffer so nothing
  hits stdout) and serialises it with rich's own `export_html` into a
  self-contained page (inline styles, MONOKAI theme, no external assets
  -- opens straight from disk). `HTML_WIDTH=120` forces the roomy
  side-by-side panel layout regardless of the exporting shell.
  `DASH_HTML_FORMAT` in report/dash.py is the wrapper page; the
  generated-at timestamp is a `%%GENERATED%%` token (not a `{brace}`
  field) so it survives rich's `str.format` call and is substituted
  after. Same privacy rule as the terminal dash -- counts/scores only,
  zero prompt content (asserted in tests and confirmed on real data).
- `serve`: stdlib only (`http.server` + `webbrowser`), zero new deps.
  Binds `127.0.0.1` (same localhost-only rule as the LLM client),
  syncs once at startup, then re-renders from the cache on every GET of
  `/` so a browser refresh is instant (no per-hit /mnt/c re-sync).
  Opens the browser automatically; degrades to just printing the URL on
  a headless box. 404s anything but `/`; one bad render returns 500
  rather than killing the server. Flags: `--since`, `--port` (0 = pick
  an open one), `--no-browser`, `--no-sync`.
- Refactor: both paths share one `_render_dash_html` helper so the file
  export and the served page can't drift. The server is built by a
  testable `_build_dash_server` that returns the HTTPServer without
  calling `serve_forever`, so tests can start it in a thread, hit it,
  and shut it down.
- 278 tests (up from 275): `test_dash_html_writes_standalone_page`,
  `test_dash_html_quiet_week_still_writes_page`,
  `test_serve_dashboard_server_responds` (real threaded server + two
  urllib fetches, asserts 200/headers/no-content/404). ruff/black clean
  (S310 added to the tests per-file-ignore for the localhost fetch, S110
  noqa on the headless webbrowser fallback), no em dashes.
- Verified live: real `serve --no-sync` run, curl'd the page (200,
  correct Content-Type, 8.9KB, panels present, 404 on other paths),
  clean Ctrl-C shutdown.
- **Known limitation (by design)**: this is the static dashboard
  auto-served, not an interactive app -- no in-page `--since` picker or
  session drill-down. Refresh reflects the current cache. An in-page
  time-range dropdown via query params (cheap, no framework) is the
  obvious next step if it's wanted; option (b), a full FastAPI app, only
  if that turns out too limiting.
Last updated: 2026-08-17 by Claude: scoring, streaks, hint mode, agent-friendly
CLI, auto mode shows refined prompt. 268 tests. ruff/black clean.

## Scoring, streaks, hint mode, and agent CLI (2026-08-17)

### Prompt scoring (0-100)
New `src/prompt_coach/analysis/score.py` -- pure deterministic scoring engine.
Each prompt starts at 50 and gains/loses points for:
- Bonuses: output format (+15), example (+10), constraints (+10), project
  context (+10), concise length (+5)
- Penalties: vague scope (-15), missing output format (-15), no example (-10),
  no constraints (-10), too short (-10), too long rambling (-10), multiple
  conflated asks (-10)
- Clamped to 0-100

`prompt-coach score "your prompt"` -- scores a prompt with breakdown.
`prompt-coach score "your prompt" --json` -- JSON output for agent consumption.

### Streak tracking
New `src/prompt_coach/streaks.py` -- tracks consecutive good prompts (score >=
70) and consecutive no-intervention prompts. Persisted to streaks.json in the
cache dir. History kept up to 1000 entries.

`prompt-coach streaks` -- shows current streaks.
`prompt-coach streaks --json` -- JSON output for agent consumption.

### Hint mode (3rd option)
New nudge mode "hint" -- non-interruptive tips that appear after the model
responds. Works via the Stop hook: the prompt goes through untouched, but if it
triggers a weak-prompt rule, a system message tip appears alongside the
response. No once-per-session gate -- every weak prompt gets a hint.

```toml
[nudge]
mode = "hint"
```

### Auto mode shows the full refined prompt
The auto mode directive now asks for the full refined prompt (not just a
one-line summary) so the user sees exactly what their prompt became:

```
Refined: <full refined prompt>
<model's answer>
```

### Agent-friendly CLI
All new commands support `--json` for machine-readable output. The agent
(Claude Code, Hermes) can run `prompt-coach score`, `prompt-coach streaks`,
`prompt-coach audit --json` and present the results conversationally.

### Config updates
`NUDGE_MODES` now includes "hint" as a 5th option alongside coach, auto,
always, and off.

## Audit trail for all nudge modes (2026-08-17)

New persistent audit log in the cache DB (~/.cache/prompt-coach/cache.db,
`audit_log` table) records every nudge intervention across all modes. Each
entry captures timestamp, session ID, source, mode, trigger, original prompt,
refined prompt (when available), and estimated token savings.

- `prompt-coach audit`: summary table (interventions, avg/total tokens saved,
  rewrites per mode)
- `prompt-coach audit --detail`: individual entries with before/after text
- `prompt-coach audit --mode auto --since 7d`: filter by mode and time range
- `prompt-coach audit --mode coach --detail --limit 10`: last 10 coach blocks

For auto mode, the refined prompt is logged as null (the session model does
the refinement internally and the hook never sees it), but the original prompt
and trigger are recorded so you can verify the intervention fired. Coach/always
modes log both original and refined.

## Per-directory nudge override docs (2026-08-17)

The `nudge.dir_overrides` config already supports per-directory mode overrides
via longest-prefix matching on the hook's `cwd`. Documented in config.toml
and the setup wizard prompts for it. Example config:

```toml
[nudge.dir_overrides]
"/home/alistair/projects/important-project" = "coach"
"/home/alistair/projects/experiments" = "off"
```

## "auto" nudge mode: hands-off prompt improvement (2026-08-10)

Alistair wanted the improvement loop hands-off: when the calibrated
triggers say a prompt needs work, improve it and send it on -- no
block, no /coach-accept. DECISIONS.md (2026-08-10, two entries) has
the full detail, including the same-session pivot. Shipped:
- `nudge.mode = "auto"` (config.py `NUDGE_MODES`): same calibrated
  triggers as coach, no once-per-session gate (auto never interrupts,
  so there's no nag cost to ration).
- Mechanism: UserPromptSubmit hooks can't replace prompt text
  (re-verified against the hook docs), and the first cut -- local-LLM
  rewrite injected as authoritative additionalContext -- was slower
  and worse-grounded than the session model itself. So auto is
  LLM-free: `_AUTO_RESPONSE` injects a directive telling the session
  model to silently refine the prompt (scope, output shape, tables for
  list-shaped answers, charts/diagrams for relationships/proportions)
  and open its reply with a one-line "Refined:" receipt. Hook latency
  ~0.5s (Python startup only); works with the desktop off.
- Two user-visible indications per firing: the hook's systemMessage at
  submit time (deterministic) and the "Refined:" line in the reply
  (directive-following, near-always).
- `_REWRITE_SYSTEM` (still used by coach/always) now also steers
  list-shaped answers toward tables/visuals.
- 275 tests: `TestBuildResponseAutoMode` (including a guard that auto
  never constructs an LLM client), config round-trip. ruff/black clean.
- Deployed: reinstalled via `uv tool install --force .`, global config
  flipped to `mode = "auto"` (email-client dir override still "off"),
  verified live against the installed binary (trigger fires, ordinary
  prompts pass untouched).

## Accept/reject slash commands for nudge blocks (2026-08-07)

Alistair asked for a button on nudge's block-and-rewrite flow, pure
quality-of-life -- the copy-paste-it-yourself step was friction on every
block. DECISIONS.md (2026-08-07) has the full detail on why real
interactive buttons aren't possible in Claude Code's hook contract and
why slash commands are the closest real equivalent. Shipped:
- `nudge.py`: when a block offers a rewrite, `{original, rewritten}` is
  now stashed keyed by session ID in a new `nudge_pending.json` (capped
  at 500 entries, same bound as the existing nudged-sessions state).
  Consuming an entry also sets a one-shot `skip_next` flag, checked at
  the top of `build_response`'s prompt path, so resending the chosen
  text doesn't immediately get held back again -- needed for `always`
  mode, which has no once-per-session gate to lean on instead.
- `cli.py`: new `nudge-consume` subcommand pops the pending entry for a
  session and prints the requested side (`rewritten` or `original`).
- `~/.claude/commands/coach-accept.md` and `coach-original.md` (global,
  outside this repo, same `!`command`` substitution pattern as the
  existing `/prompt-coach` command): run `nudge-consume` and resend its
  output as the next message.
- `_block_reason()` now points at the two commands instead of "paste
  that in if you want it."
- 269 tests (up from 260): `TestPendingRewrite` in test_nudge.py, two
  CLI round-trip tests in test_cli.py. ruff/black clean, no em dashes.
- Reinstalled via `uv tool install --force .` and verified live: a real
  block against the desktop Ollama model, then `nudge-consume` for both
  `rewritten` and `original`, against the installed binary.

## Live feedback round: grounded rewrites + two bug fixes (2026-07-31)

Alistair used the hook outside this repo; DECISIONS.md (2026-07-31) has
the full detail. Shipped:
- `gather_context()` in nudge.py: the rewrite LLM now gets a SESSION
  CONTEXT block (cwd, nearest project doc excerpt via the docs-quality
  finder, last 3 human prompts tailed from the transcript). Live smoke
  against real desktop Ollama: rewrite went from generic to naming this
  repo's actual conventions and paths. Still verbose -- `_REWRITE_SYSTEM`
  tuning remains on the pick-up list.
- **Bug (seen live)**: nudge blocked a `<task-notification>` background-
  task event and offered to "rewrite" it. New `_is_machine_prompt` guard
  (harness wrapper tags + `classify_origin`) covers coach/always/Stop/
  Hermes paths; machine payloads no longer burn the session gate either.
- **Bug (seen live)**: `/prompt-coach setup` died with "Aborted." (no
  TTY for the wizard). Now catches click's Abort, prints a run-it-in-a-
  real-shell pointer, exits 0.
- `uv tool install --force .` re-run and fixes verified against the
  installed binary (the hook's actual target).

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
**Resolved 2026-07-30** (was 0a-0e above; see DECISIONS.md for full detail
on each): Claude Code hook re-enabled in `~/.claude/settings.json`
(pointed at the installed binary, not `uv run`); Hermes's `pre_llm_call`
wired into `~/.hermes/config.yaml` and verified via `hermes hooks test`
(one real gap remains: Hermes's own first-use consent prompt hasn't fired
yet in an actual `hermes chat` session - expected, left for Alistair to
click through once); `install.sh` run for real, found and fixed the
ANSI-color-leak bug; cache cleared and resynced, model-fit coverage now
458/712 (was 16/916); model-fit and nudge-keyword thresholds checked
against the real backfilled data and found already well-calibrated - no
changes made, the "no change needed" finding is recorded rather than
silently assumed.

1. **Resolved 2026-07-31**: rewrite path run live (Alistair in the wild,
   plus grounded-context smokes against real Ollama). `_REWRITE_SYSTEM`
   tuned same day: "context is background for YOU, not content for the
   rewrite" + a length bound cut rewrites from mini-spec (recited doc
   boilerplate) to ~3 grounded sentences, verified on both trigger shapes
   (long-unshaped in prompt-coach, short-vague in email-client -- the
   second correctly cited that project's design-pack/ and BLUEPRINT.md).
   `_block_reason()` wording unchanged and reads fine in practice.
2. `always` mode's per-prompt LLM latency (bounded by `nudge.llm_timeout`,
   default 20s) hasn't been felt in practice - try it for real before
   deciding whether 20s is too generous/stingy, and whether `coach` mode's
   silent LLM-unreachable-once-per-session probe (an `httpx.get` on every
   trigger) is worth caching.
3. **Resolved 2026-07-30**: re-checked the short-vague keyword list
   against a fresher, larger corpus - trigger rate held stable at 1.5%
   (matching the original 2026-07-27 calibration exactly). No drift, no
   change made. See DECISIONS.md.
4. Optional: ChatGPT importer verification if a real export ever shows up
   (`prompt-coach import <export.zip>`, clear UNVERIFIED markers here and
   in BLUEPRINT.md 16.2) - no longer the priority gap, see DECISIONS.md.
5. Optional: machine-prompt classifier misses cron-generated curator prompts
   ("You are curating..."); refine if the machine segment matters more.
6. Optional: Codex store only has one live session to validate against;
   revisit extraction rules (the `## My request for Codex:` wrapper strip,
   the `plain`/`environment_context` kind split) once more real usage
   accumulates, especially non-VS-Code (raw terminal) sessions.
7. Phase 3 candidates (blueprint section 15): trends over time, serve API
   (**resolved 2026-08-17**, see below), OpenWebUI store, weekly coaching
   report via Hermes cron.
8. Doc-quality thresholds (`_SPARSE_WORDS=150`, `_STALE_DAYS=90`) are
   first-guess; tune once there's a project you'd actually flag.
9. **Resolved 2026-08-17**: local web UI shipped -- `dash --html <file>`
   (static self-contained page) and `serve` (localhost web server that
   opens it in a browser), both reusing the terminal dashboard's
   renderable, zero new deps. Took option (a) over (b): no framework, the
   static render auto-served covers "look at my dashboard in a browser".
   Follow-on if wanted: an in-page `--since` dropdown via query params
   (cheap), or option (b)'s full FastAPI app only if the static page turns
   out too limiting. See the top-of-file entry and DECISIONS.md.

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
