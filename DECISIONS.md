# Decisions & Changes - prompt-coach

## 2026-07-20 - Blueprint v2: store facts corrected against live systems

**Trigger**: v1 blueprint (deepseek-v4-flash) claimed Hermes sessions live at
`~/.hermes/data/sessions.db` with sessions/messages/profiles tables "from
inspection". Live inspection found no such file and no profiles table.
**Decision**: Hermes store is `~/.hermes/state.db` (sessions + messages
tables, `active`/`compacted` flags, unixepoch timestamps, FTS5 present).
Extraction filter: `role='user' AND active=1 AND compacted=0`. All 281
current user rows have compacted=0; the filter is defensive.
**Why**: every downstream task treats the blueprint as ground truth;
confident invention is exactly the failure mode prompting-standards A8 warns
about, and it came from skipping inspection.
**Affects**: Architecture, technical spec, Hermes store reader and its tests,
AGENTS.md.
**Decided by**: Architect (Claude) after direct DB inspection; approved by
Alistair.

## 2026-07-20 - Phase-1 scope: Hermes + Claude Code stores; OpenWebUI deferred

**Trigger**: Claude Code history (`~/.claude/projects/*/*.jsonl`, 108 main
transcripts, 691MB) dwarfs the Hermes corpus (62 sessions, 281 user
messages), and v1 ignored it entirely. OpenWebUI's DB is remote (ollama VM)
and needs auth.
**Decision**: Phase 1 reads Hermes state.db and Claude Code JSONL (plus
generic JSON import). OpenWebUI, llm-api usage-log enrichment, trends, and
the serve API are deferred to phase 2.
**Why**: coaching quality is bounded by corpus coverage; Claude Code is ~95%
of the real history. Claude Code user lines carry `promptSource: "typed"` and
`origin.kind: "human"`, giving reliable human/machine separation for free.
**Affects**: Store readers, content inventory, build plan size.
**Decided by**: Alistair, 2026-07-20 session.

## 2026-07-20 - Synchronous code with stdlib sqlite3; ORM and async deps dropped

**Trigger**: scaffold pyproject carried sqlalchemy, aiosqlite,
pytest-asyncio.
**Decision**: fully synchronous pipeline on stdlib `sqlite3`; remove
sqlalchemy, aiosqlite, pytest-asyncio.
**Why**: linear batch CLI with no concurrent I/O to hide; the LLM bottleneck
is one local GPU that serialises requests anyway. An ORM over four read-only
SELECTs against a schema this project does not own adds risk, not value.
Reversible later with a contained ThreadPoolExecutor around LLM calls.
**Affects**: Dependencies, every data-access module, test configuration.
**Decided by**: Architect (Claude); approved via plan review.

## 2026-07-20 - Claude Code executes all build tasks

**Trigger**: hermes-skills DECISIONS.md carries an unresolved 2026-06-19
proposal to collapse model routing to 2 tiers; the original v1 blueprint
assumed multi-tier hermes -z local routing for the build.
**Decision**: Claude Code (interactive session or claude -p) executes every
build task. No local-model routing for the build. Runtime analysis remains
local (separate concern).
**Why**: Alistair's call in the 2026-07-20 planning session ("just do it
all"): any single default beats a multi-tier system that stalls on routing
decisions.
**Affects**: Model strategy, all build task Model fields, handoff prompts.
**Decided by**: Alistair, 2026-07-20 session.

## 2026-07-20 - Runtime LLM is local-only, with a private-address guard

**Trigger**: pointing prompt-coach at the llm-api gateway could silently ship
prompt history to OpenRouter via the gateway's fallback routing, breaking the
product's core privacy promise.
**Decision**: LocalLLM refuses any base URL that is not localhost or RFC1918
unless `allow_remote = true` is explicitly set in config. Default endpoint is
direct desktop Ollama (192.168.1.123:11434, plain HTTP on the LAN, accepted
knowingly). No cloud keys in config. Deterministic-only degraded mode is
first-class (the desktop GPU is frequently off; it was off during design).
**Why**: the privacy moat is the product; it must be enforced in code, not
just documented.
**Affects**: LLM client, config schema, data & privacy spec, report
degraded mode.
**Decided by**: Architect (Claude); approved via plan review.

## 2026-07-20 - Machine-generated prompts are segmented, not discarded

**Trigger**: many Hermes "user" messages are `hermes -z` one-shot task specs
authored by the orchestration pipeline, not typed by Alistair.
**Decision**: classify every prompt human/machine at extraction. All metrics
and rubric scores aggregate per segment, side by side; the human segment is
the primary coaching subject.
**Why**: mixing segments corrupts the human coaching signal, but the machine
segment is valuable on its own: scoring it against prompting-standards Part A
audits the orchestration pipeline's blueprint/task-spec quality.
**Affects**: Store extraction, metrics, rubric aggregation, report layout.
**Decided by**: Architect (Claude) proposal; approved by Alistair (rubric
question, 2026-07-20).

## 2026-07-20 - Unified retrieval via cache FTS5; Hermes FTS not used

**Trigger**: Hermes state.db already has FTS5 tables over messages.
**Decision**: `query` retrieval uses one FTS5 index in the local cache DB
covering all stores; Hermes's own FTS is not queried.
**Why**: Hermes holds ~2% of the corpus; a second retrieval path for it adds
complexity without coverage. Its FTS remains available for a possible later
`--live` mode.
**Affects**: Cache schema, query implementation.
**Decided by**: Architect (Claude, on Plan-agent recommendation).

## 2026-07-20 - Micro-replies excluded from rubric/pattern scoring, kept in metrics

**Trigger**: first live sync surfaced that ~30% of hand-typed prompts are
micro-replies ("1", "y", "continue"): real input, but not prompt writing.
Scoring them against a prompt-authoring rubric drowned the signal (they would
all score zero on every rule).
**Decision**: prompts under 40 characters are excluded from rubric scoring
and pattern sampling. They remain in the cache and in the style metrics,
where the tiny median prompt length is itself honest coaching data.
**Why**: the rubric measures how well prompts are written where there is a
prompt to write; a wall of zero-scored acknowledgements is noise dressed as
signal.
**Affects**: rubric scoring, pattern sampling, rubric tests.
**Decided by**: Tester gate finding (Claude), 2026-07-20.

## 2026-07-20 - Phase 2 scope: llm-api integration skipped entirely

**Trigger**: proposal to attach prompt-coach to llm-api. Live inspection
showed llm-api's UsageLog stores metadata only (tokens, cost, provider,
latency) and by explicit design never message content, so "analyse llm-api
prompts" is impossible without either a privacy-posture change in that repo
(opt-in content capture) or settling for metadata-only enrichment.
**Decision**: neither, for now. Phase 2 does not touch llm-api at all.
Revisit when there is a concrete need.
**Why**: Alistair's call (2026-07-20): the coaching value lives in the
content stores; metadata enrichment is nice-to-have, and content capture is
a privacy design change not worth opening speculatively.
**Affects**: Phase 2 scope: Copilot store, ChatGPT export import, and the
dash visualisation only.
**Decided by**: Alistair, 2026-07-20 session.

## 2026-07-20 - Copilot Chat store: verified real, read via /mnt/c

**Trigger**: phase-2 proposal listed Copilot as verify-first-or-drop.
Nothing exists under ~/.vscode-server on WSL, but the Windows VS Code
profile has 155 chat session files across 154 workspaces at
C:\Users\alistair\AppData\Roaming\Code\User\workspaceStorage\<ws>\chatSessions\<uuid>.jsonl,
readable from WSL via /mnt/c without ssh.
**Decision**: add a Copilot store reading that path (config override
PROMPT_COACH_COPILOT_DIR). Format is a JSON-patch event log per line:
kind 0 carries initial state (v.requests[]), kind 2 with k=["requests"]
appends request objects; each request has message.text (the typed prompt),
requestId, and an epoch-ms timestamp. kind 1 events mutate existing paths
and carry no new prompts, so append-only offset resume stays valid.
**Why**: verified against real files before designing; the reader replays
only the two event kinds that carry prompts instead of full patch replay.
**Affects**: models (new source kind), stores, cache sync, discover, dash.
**Decided by**: Architect (Claude) after live inspection, 2026-07-20.

## 2026-07-20 - ChatGPT: export-import only, format unverified

**Trigger**: "local ChatGPT conversations" do not exist; ChatGPT offers only
the official data-export ZIP containing conversations.json. No export file
is present on this machine.
**Decision**: build the importer against the documented export format
(conversations list, mapping tree, author.role=user, content.parts) and
accept both the ZIP and the extracted conversations.json. Marked UNVERIFIED
in STATUS.md until run against a real export.
**Why**: designing against a documented format is acceptable when flagged;
inventing a local store is not.
**Affects**: import command (auto-detects format), stores, STATUS.
**Decided by**: Alistair (approved improved prompt), 2026-07-20.

## 2026-07-20 - dash: deterministic-only visualisation, no prompt content

**Trigger**: phase-2 ask for a "beautiful CLI visualisation".
**Decision**: `prompt-coach dash` renders with rich (already a dependency
via typer): per-store volume sparklines, human/machine split, style
metrics, and the deterministic rubric scorecard. No LLM calls, no prompt
content on screen (counts, rates, and scores only), auto-degrades on
non-TTY plus a --plain flag.
**Why**: the dashboard must be instant and privacy-clean; LLM insights
belong in `report`, which is already cached and bannered.
**Affects**: CLI, report/dash rendering, tests.
**Decided by**: Alistair (approved improved prompt), 2026-07-20.

## 2026-07-21 - Default model switched to qwen3-coder-30b:latest (32k num_ctx)

**Trigger**: phase-1/2 gates deferred the live-LLM smoke; pick-up item said
to check the model's effective num_ctx before raising batch sizes.
**Decision**: default model is now `qwen3-coder-30b:latest`, the desktop's
derived tag with num_ctx 32768 baked in. The previous default
`qwen3-coder:30b` has no num_ctx parameter, so it runs at the Ollama server
default (4096, no OLLAMA_CONTEXT_LENGTH set on the desktop) and silently
truncates pattern-map payloads (_MAX_PAYLOAD_CHARS 32000, roughly 8k tokens).
**Why**: silent truncation corrupts pattern insights with no error surface;
the derived tag is already the roster standard on this host.
**Affects**: config.py DEFAULT_MODEL, README, AGENTS.md, BLUEPRINT sections
4e/9/15.
**Decided by**: Claude (Fable 5) during live smoke, 2026-07-21.

## 2026-07-21 - New store: Codex CLI; ChatGPT verification deprioritized

**Trigger**: after the live-LLM smoke, Alistair flagged that the actual
gap is CLI agent harnesses ("more codex than chatgpt... this is more about
cli agent harnesses etc for now"). Checked the filesystem: a real
`/mnt/c/Users/Alistair/.codex/sessions/` directory exists with one genuine
session (VS Code extension, Oct 2025), confirming Codex CLI is an actual
source worth reading, unlike the ChatGPT web export which has no local
store at all and needs a manually-provided ZIP that doesn't exist yet.
**Decision**: build stores/codex_cli.py against the live-verified rollout
JSONL format (BLUEPRINT.md 16.4a) as a first-class store alongside Hermes,
Claude Code, and Copilot. The ChatGPT importer stays as built (manual
import command, UNVERIFIED format) but drops off the active pick-up list
until a real export shows up - it is no longer the priority gap.
**Why**: prompt-coach's value is reading session stores that already exist
on the machine; Codex CLI is one, the ChatGPT web export is not (it is an
opt-in manual import for a source the user doesn't actually use this way).
**Affects**: models.py (SourceKind.CODEX), config.py (codex_dir), cli.py
(default_stores), new store + tests, docs (README, AGENTS, BLUEPRINT
16.4a, STATUS).
**Decided by**: Alistair (redirected scope), 2026-07-21.

## 2026-07-22 - Doc-quality scores docs that exist, not presence; redirect stubs excluded

**Trigger**: Alistair asked whether "context" (project docs) factors into
analysis at all. A first spike checked presence of CLAUDE.md/AGENTS.md/
README.md across the real corpus's project dirs: 31 of 34 already have at
least one, so presence has no variance to score. A second spike measured
the docs that exist instead (size, header/list structure, git staleness)
and found real variance (46 files, 2-3290 words) - but 9 of those had zero
structure, and manual inspection showed they were all tiny CLAUDE.md files
reading "See AGENTS.md." - a deliberate redirect pattern across ~8
projects, not a quality gap.
**Decision**: `analysis/docs.py` scores size/structure/staleness of the
nearest project doc(s) found by walking up from each prompt's `cwd`, but
detects redirect stubs (short text naming another doc file) and never
flags them. Thresholds (`_SPARSE_WORDS=150`, `_STALE_DAYS=90`,
`_REDIRECT_MAX_WORDS=30`) are first-guess, not tuned against a labelled
set - flagged as revisit-if-noisy on the pick-up list.
**Why**: shipping the naive size-only version would have produced a
confidently wrong "your CLAUDE.md is too sparse" flag on every project
using the redirect pattern - the same "confident invention" failure mode
the phase-1 blueprint correction (2026-07-20 entry) already flagged once.
**Affects**: models.py (DocFinding, DocQualitySummary), new
analysis/docs.py, report/dash.py (new panel), cli.py (dash wiring).
**Decided by**: Alistair (spiked both angles), 2026-07-22.

## 2026-07-22 - Live nudge hook: deterministic only, once per session

**Trigger**: Alistair wanted a live, Claude-Code-tips-style nudge wired
into prompt-coach. Calibration against the real corpus before building
anything: 77-97% of substantial (>=100-300 char) human prompts already
fail the A5 (output contract) + A6 (worked example) rubric rules. Firing a
tip on every rule failure would nag on nearly every prompt submitted -
exactly the annoying-toast failure mode flagged when this was first
scoped.
**Decision**: `prompt-coach nudge` wires into Claude Code's
`UserPromptSubmit` hook (verified contract: stdin JSON with `prompt`/
`session_id`, stdout JSON with `systemMessage` for a non-blocking tip).
Deterministic-only (no LLM call - hooks run synchronously and block
submission). Fires at most once per session, and only for prompts >=200
chars (rubric.py's own A4 threshold) that fail both A5 and A6. Never
raises: malformed stdin produces `{}` rather than blocking every prompt
you type on any bug. Scoped to Claude Code only for v1 - Copilot/Codex/
Hermes have no equivalent hook mechanism to wire into.
**Why**: the whole feature is worthless if it degrades into constant
noise; better to fire rarely and usefully than often and ignorably.
**Affects**: new nudge.py, cli.py (`nudge` command), global
`~/.claude/settings.json` (UserPromptSubmit hook registration).
**Decided by**: Alistair ("both and build"), 2026-07-22.

## 2026-07-27 - CLI visual polish pass: dash/stats/report, exit 0 for empty ranges

**Trigger**: CLI_DESIGN_BRIEF.md flagged three inconsistencies (`stats` bare
`echo` vs `dash` full `rich`; score bands only colored, never labeled;
`--plain`/`report` overlap unexplained) and asked for an explicit look at
every degraded state, not just the happy path. Resolved via a visual design
pass (CLI Visual Spec, decisions D1-D9).
**Decision**: `score_band()`/`score_label()` (models.py) are the one
source of the good/fair/weak/n/a mapping, used by `dash`'s rich `Text` and
`report`'s markdown alike. `stats` upgrades to a rich table matching
`dash`'s conventions (header line, dim counts) but keeps its human/machine
split and adds no score-band colors (rates are behavior, not quality).
`dash`'s volume panel orders claude-code first then by volume, stacks
panels full-width below 100 cols, collapses extra stores to "+N more" when
narrow, and drops (not just recolors) the sparkline in `--plain` while
keeping the column present so table geometry doesn't shrink enough to
word-wrap the panel title (found live while smoke-testing `--plain`).
Rubric rows with coverage < 3 render dim with a "low n" suffix, distinct
from n/a. The docs panel says "docs · clean" instead of silently
disappearing when there are no flagged findings. Zero-prompts-in-window and
first-run-no-cache now exit 0 (were exit 1) across `dash`, `stats`, and
`report` -- neither is an error, and the message distinguishes "sync
never ran" from "quiet week".
**Why**: a routine personal-use CLI checked every morning should never
present "nothing happened" as a failure; the visual/plain-text split needs
one shared semantic mapping or it silently drifts (the brief's own #2). The
mockup's single-column scorecard and ultra-compact plain-text volume line
were illustrative simplifications, not requirements -- kept the existing
two-column (human/machine) data model rather than discarding real
structure to match the mockup literally.
**Affects**: models.py (`score_band`/`score_label`/`LOW_N_THRESHOLD`),
report/dash.py, report/generator.py + templates/report.md.j2, cli.py
(`dash`/`stats`/`report` commands), tests (exit-code and rendering
assertions updated).
**Decided by**: Claude (Sonnet 5), implementing CLI Visual Spec per
Alistair's request, 2026-07-27.

## 2026-07-27 - Per-store sync progress bar, on stderr

**Trigger**: Alistair noticed `dash`/`stats`/`report`/`cache sync` show no
output until the whole sync completes, then the full render appears at
once -- looks hung, especially since Copilot's /mnt/c reads routinely
dominate wall clock (STATUS.md: 17.5s sync, "almost all of it /mnt/c stat
overhead").
**Decision**: `CacheDB.sync()` takes an optional `on_store(kind, done)`
callback, fired right before and right after each store. `cli.py`'s new
`sync_with_progress()` wraps every sync call site (`dash`, `stats`,
`report`, `query`, `cache sync`) in a transient rich progress bar (spinner
+ N/4 bar + current store name) on a dedicated stderr `Console`. Falls
back to a silent sync when stderr isn't a terminal (piped output, `--out`
to a file, or under test) -- there's no one to show a bar to, and it must
never leak progress text into redirected stdout.
**Why**: per-store granularity (not per-line) is enough to fix "looks
hung" without adding new logic to the store readers themselves; stderr
keeps it out of `report`'s markdown (`--out`/`>` redirection) and out of
`dash --plain`'s scriptable stdout.
**Affects**: cache.py (`sync` signature), cli.py (all sync call sites),
tests (`test_on_store_callback_fires_start_and_done_per_store`).
**Decided by**: Alistair (raised the "looks hung" issue), 2026-07-27.

## 2026-07-27 - Nudge also fires on Stop, alongside UserPromptSubmit

**Trigger**: Alistair asked whether coaching tips could appear after Claude's
response, not just before submission. Verified against the current Claude
Code hooks docs (code.claude.com/docs/en/hooks, fetched live -- a prior
subagent's recollection of some details was cross-checked, not trusted
outright): `Stop` fires once per turn, after Claude finishes responding;
its stdin carries `session_id` and `transcript_path` but no prompt text
(unlike `UserPromptSubmit`); its output supports the same non-blocking
`systemMessage` field.
**Decision**: `prompt-coach nudge` now handles both events from one
process, branching on stdin's `hook_event_name`. For `Stop`, it recovers
the just-submitted prompt by tailing the transcript JSONL from EOF in
growing 64KB windows (up to ~256KB) and reusing
`stores/claude_code.py`'s `parse_line` acceptance filters -- not a second
parser. Both events share the existing once-per-session gate
(`nudge_state.json`), so whichever moment (before submission or after
response) first catches a weak prompt is the only one that speaks; the
other stays silent for the rest of that session. Wired into
`~/.claude/settings.json` as a second `Stop` hook entry, same command as
`UserPromptSubmit`.
**Why**: alongside, not instead -- Alistair wants both moments available.
Sharing the gate keeps the "never nag" design intent (2026-07-22 entry)
intact rather than doubling the nag budget per session. Tailing beats
reading the whole transcript because sessions run to hundreds of MB and
the hook must stay near-instant.
**Affects**: nudge.py (`_last_human_prompt`, `hook_response_stop`), cli.py
(`nudge` command dispatch), `~/.claude/settings.json` (global, not in this
repo), tests (test_nudge.py, test_cli.py).
**Decided by**: Alistair ("alongside"), 2026-07-27.

## 2026-07-27 - Second nudge trigger: short prompts with unconstrained broad scope

**Trigger**: Alistair asked what happens when a short prompt is vague *by
what it asks for* -- e.g. "redo everything" -- since nudge's only trigger
required >=200 chars, missing exactly this case. Spiked against the real
corpus before adding anything (per the project's standing practice of
checking live data rather than guessing, see the 2026-07-20/22 entries):
1,739 human prompts, 91% under 200 chars, and 26 of those (1.5%) combine a
broad-scope word with zero stated constraints. Every one of those 26 also
lacked example/format, so it's the same underlying "no shape" problem, just
too short to trip the length gate. The keyword list guessed up front
(redesign/rewrite/refactor/overhaul/restructure) never appeared once in the
real data; what actually fired was everything/all of/whole/revamp.
**Decision**: `nudge.py` gets a second, independent trigger,
`_is_short_and_vague()`: prompt under 200 chars, matches
`_BROAD_SCOPE` (the four calibrated words only), and `has_constraints()` is
false. It gets its own tip text (`_TIP_SCOPE`, about naming what's in/out
of scope) rather than reusing the output-format tip, since the two
failure modes read differently to the person hitting them. Both triggers
share the existing once-per-session gate and the `_tip_for()` dispatcher
used by both UserPromptSubmit and Stop.
**Why**: the length gate was a proxy for "substantial task ask," and a
proxy that misses "short but sprawling" asks is exactly the failure mode
Alistair caught. Using the corpus-verified keyword list instead of the
initially-guessed one avoids repeating the mistake this project has
flagged twice before (2026-07-20 blueprint correction, 2026-07-22 docs.py
redirect-stub false positive): confident invention where inspection was
available and cheap.
**Affects**: nudge.py (`_is_short_and_vague`, `_tip_for`, `_TIP_SCOPE`,
`_BROAD_SCOPE`), tests (test_nudge.py `TestShortVagueScope`).
**Decided by**: Alistair (spiked and confirmed before implementing),
2026-07-27.

## 2026-07-27 - Nudge can block-and-rewrite via LLM; new "coach"/"always"/"off" modes

**Trigger**: Alistair asked for prompt-coach to actually improve his prompt
and offer it to run, not just tip him. Checked Claude Code's hook contract
before designing anything (docs.claude.com -> code.claude.com redirect,
fetched live, a first pass self-contradicted and was re-verified): no hook
can rewrite/replace the literal prompt text or auto-resubmit anything.
`UserPromptSubmit` can only block (`decision: "block"` + `reason`, prompt
never reaches Claude) or proceed. `Stop`'s `decision: "block"` means
something structurally different (forces the turn to continue) -- not a
substitute.
**Decision**: `nudge` gets three modes via new `NudgeConfig`
(`PROMPT_COACH_NUDGE_MODE` env / `[nudge] mode` in config.toml, default
`"coach"`): **coach** -- the existing calibrated triggers (see the two
entries above), once per session, now BLOCK with an LLM-generated rewrite
in the reason text (paste-it-in-yourself, nothing auto-runs) when the local
LLM is reachable, degrading to the old non-blocking tip otherwise.
**always** -- every `UserPromptSubmit` is blocked and rewritten regardless
of quality/session history (explicit opt-in, since it adds LLM latency to
every prompt); if the LLM is unreachable the prompt is let through
unmodified rather than blocking with no way out. **off** -- silences both
hooks entirely. `Stop` only ever does the old tip-only behavior, and only
in coach mode (in always mode every prompt is already caught
pre-submission; Stop's block semantics don't fit "offer a rewrite" anyway).
The rewrite call uses `complete_json` (existing LLM client) with its own
short timeout (`nudge.llm_timeout`, default 20s) separate from
`report`/`query`'s 120s default, since this runs inline in a hook.
`~/.claude/settings.json`'s `UserPromptSubmit` hook timeout raised from 5s
to 25s to give that call room.
**Why**: "offer to run it" has no literal hook API, so blocking + showing
the rewrite in the reason is the closest real equivalent -- the user reads
it and chooses to paste it in, nothing happens automatically. Defaulting
to "coach" (not "always") preserves the low-latency, LLM-optional
behavior that's been the design principle since 2026-07-22; "always" is
opt-in because it changes that tradeoff on purpose.
**Affects**: config.py (`NudgeConfig`, `Config.nudge`), nudge.py
(`build_response`, `rewrite_prompt`, `_make_llm`, `_rewrite_or_fallback`;
removed `hook_response` in favor of `build_response`), cli.py (`nudge`
command simplified to one call), `~/.claude/settings.json` (hook timeout),
tests (test_nudge.py mode-dispatch classes, test_cli.py always/off-mode
CLI tests).
**Decided by**: Alistair ("Block it but there should be an option... that
just blanketly improves every single prompt", "Call the local LLM"),
2026-07-27.
