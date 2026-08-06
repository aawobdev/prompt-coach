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

## 2026-07-29 - New analysis dimension: model fit (scope + surface decided, not yet built)

**Trigger**: Alistair proposed scoring how well each prompt fits the model
that handled it, calibrated against whichever models are actually
available to the user, not a hardcoded catalog. Spiked before designing:
confirmed a model field exists in every store, but at different
granularity. Hermes and Codex CLI capture it at session level
(`sessions.model`; Codex's `gpt-5-codex` seen in the one live session).
Claude Code captures it per turn, and sessions genuinely mix models: live
transcripts show `claude-fable-5`/`claude-opus-4-8`/`claude-sonnet-5`
within one conversation, plus a non-model `<synthetic>` placeholder value
that must be excluded. Copilot carries `selectedModel.identifier` per
request (`copilot/claude-haiku-4.5`, `ollama/Ollama/devstral-24b:latest`),
including a `copilot/auto` value where Copilot chose the model itself and
no single model can be attributed to that turn.
**Decision**: (1) "Available models for the user" is derived, never
configured: empirically, from distinct models already observed in the
user's own history per store, which generalizes to 2 models or 20,
local-only or mixed, with nothing to hand-maintain; for Ollama-style
endpoints this is supplemented by querying the live models list
`LocalLLM.available()` already hits against `GET {base}/models`, so
installed-but-unused local models are visible too. Claude Code, Copilot,
and Codex have no discovery endpoint, so those stay empirical-only. (2)
Model fit gets both a descriptive mode (flag a mismatch only) and a
prescriptive mode (suggest a specific better-fit model from the user's own
observed/installed set), user-selectable, mirroring nudge's existing
coach/always/off mode pattern (`NudgeConfig`) rather than picking one
behavior for everyone. (3) Surface is report/dash first: retrospective,
over the sampled corpus, reusing the existing rubric/patterns sampling
infra (150-300 prompts) with no new per-prompt LLM cost; a live
nudge-time check is an explicit later phase, not built now. (4) Per-turn
granularity is required for Claude Code and Copilot, not per-session,
since the model can change mid-conversation; confirm whether Codex CLI is
truly session-only before assuming it needs no per-turn handling.
**Why**: avoids two failure modes already on record in this file:
inventing a static model list (same "confident invention" pattern as the
2026-07-20 blueprint correction and the 2026-07-22 docs.py redirect-stub
catch) and adding unsampled bulk-LLM cost (the same B7 rule already
governing rubric/patterns). Mirroring nudge's mode pattern instead of
inventing a new config shape keeps the config surface consistent across
features.
**Affects**: new analysis dimension, not yet built (no module or task
numbers assigned yet); a config schema addition analogous to
`NudgeConfig`; store readers may need per-turn model capture where not
already present; a new report/dash panel; BLUEPRINT.md needs a new
phase section once the deterministic "what does the prompt demand" signal
set is designed.
**Decided by**: Alistair (scope: "both but give the user a choice, like
with the prompt improvement"; surface: "both, report first"), 2026-07-29.

## 2026-07-29 - Model fit shipped: analysis/model_fit.py, wired into report + dash

**Trigger**: the scope/surface decision above ("ship it").
**Decision**: built per the decision entry: `Prompt.model` added (all four
live stores capture it -- Hermes session-level from `sessions.model`;
Claude Code and Codex CLI per-turn via a pending/flush buffer, resolved
from the assistant reply's `parentUuid` and the following `turn_context`
line respectively, both confirmed live; Copilot per-request `modelId`,
including the unattributable `copilot/auto` value). `cache.py`'s schema
gained a `model` column with an `ALTER TABLE` migration guard for
pre-existing cache.db files. `analysis/model_fit.py` is deterministic
only: `classify_model_tier` uses Claude's public haiku/sonnet/opus naming
plus a param-count regex for local models, returning None (unclassified,
not guessed) for anything else, including `gpt-5-codex` and `claude-fable-5`
-- no documented ladder exists for either, so neither is scored.
`estimate_demand_tier` is a plain char-length bucket (first-guess
thresholds, not calibrated). `ModelFitConfig.mode` (off/descriptive/
prescriptive, default descriptive) mirrors `NudgeConfig`; prescriptive
suggestions are scoped to the same store's own observed model set only
(you can't act on a suggestion from a different harness). Wired into both
`dash` (new panel, counts/tiers/direction only, no prompt content) and
`report` (new Model Fit section) per the "report first" surface decision.
213 tests passing (up from 186), ruff and black clean.
**Live-smoke finding**: `discover` and a full `--refresh` sync ran clean
against the real corpus (65 Hermes sessions, 87 Claude Code, 167 Copilot,
1 Codex), but coverage came back at only 16/916 eligible prompts -- the
cache dedupes inserts on `(source, session_id, message_ref)`, so prompts
already cached from before this feature existed keep `model=NULL`
forever; only prompts synced fresh after this change (or after a
`cache clear` + resync) get a model attached. This is a one-time
backfill gap, not a bug in the ongoing behavior, but it means the report/
dash panels will under-report until either time passes or the cache is
cleared once.
**Why**: matches the decision entry's constraints exactly (empirical
availability, no bulk LLM cost, per-turn where the store supports it,
descriptive/prescriptive as a mode not a fork). The coverage gap was
found by testing live against real data rather than assuming the schema
change would apply retroactively -- the same discipline this project has
applied at every prior gate.
**Affects**: models.py (`Prompt.model`, `ModelFitFinding`,
`ModelFitSummary`, `ReportData.model_fit`), config.py (`ModelFitConfig`),
cache.py (schema + migration), all four store readers, new
analysis/model_fit.py, report/dash.py, report/templates/report.md.j2,
cli.py, tests (test_model_fit.py new; store/cache/nudge test fixtures
updated for the new field).
**Decided by**: Claude (Sonnet 5), implementing the 2026-07-29 scope
decision per Alistair's "ship it", 2026-07-29.

## 2026-07-29 - nudge hook latency: confirmed real, fixed via lazy imports

**Trigger**: Alistair had disabled the nudge hook entirely (`~/.claude/
settings.json` `hooks: {}`, confirmed by inspection), suspecting it was
adding delay to every prompt. Tested live rather than assumed: timed real
`prompt-coach nudge` invocations (exact `UserPromptSubmit` payload shape)
across mode=off, non-triggering, triggering (LLM reachable/unreachable),
and always mode, plus `python -X importtime` to isolate where the time
actually went.
**Decision**: the suspicion was correct, but the cause was not the LLM --
`cli.py` imported the entire CLI surface (report generation, rubric/
pattern analysis, all four store readers) at module scope, so typer
dispatching to `nudge` alone paid for all of it; `nudge.py` separately
imported `llm.client` at module scope, which imports the `openai` SDK's
full type surface (Assistants/Threads/Batches/Evals APIs never used here)
at a measured ~700-900ms. Every single prompt submission paid this
regardless of mode or whether a trigger fired. Fixed by moving all
command-specific imports in `cli.py` to be local to the functions that use
them (mirroring the local-import pattern already used for `rich`/`query`/
`nudge` imports there), and moving `nudge.py`'s `llm.client` import into
`rewrite_prompt()`/`_make_llm()`, the only places it's actually used.
Return-type annotations referencing the now-lazy `LocalLLM` are guarded
under `if TYPE_CHECKING:` so ruff's F821 stays clean without paying the
import cost (the annotations themselves are never evaluated at runtime,
since `from __future__ import annotations` is already active everywhere
in this codebase).
**Measured effect**: mode=off / non-triggering (the common case for most
prompts most sessions) dropped from ~1.3-1.5s to ~0.4-0.5s per prompt
submission -- roughly 3x. The residual ~0.4-0.5s is `uv run` + typer/rich
startup, inherent to shelling out to any Python CLI per hook invocation,
not something this fix touches. Coach mode's first trigger of a session
still costs ~2.8s warm (real local-model inference, not import overhead)
or ~3.1s when the LLM is unreachable (bounded by `LocalLLM.available()`'s
2s probe timeout) -- both by design, once per session, not a bug.
**Why**: a hook that runs on every keystroke-adjacent prompt submission
must be judged against that frequency; a ~1s constant tax regardless of
whether nudge does anything that turn is exactly the "quiet but real" cost
that erodes trust in a tool meant to be used daily. Root-caused with
`python -X importtime` rather than guessing which import was slow.
**Affects**: cli.py (import structure only, no behavior change), nudge.py
(import structure only). 213 tests unchanged and passing, ruff/black
clean. Hook is still NOT re-registered in `~/.claude/settings.json` --
that's Alistair's call now that the cost is measured and fixed.
**Decided by**: Claude (Sonnet 5), testing per Alistair's "run" on the
2026-07-29 improved prompt, 2026-07-29.

## 2026-07-29 - Setup experience: installer script + wizard, modelled on Hermes's actual CLI

**Trigger**: Alistair wanted a "standalone binary/script" setup like the
Hermes CLI, with per-store enable/disable and the ability to trigger
reports from setup. Checked Hermes's actual setup live rather than
guessing what "like Hermes" means: `~/.hermes/hermes-agent/setup-
hermes.sh` creates a venv and symlinks a `hermes` wrapper into
`~/.local/bin`; `hermes setup` is a separate interactive wizard
subcommand; `hermes` itself is not a compiled binary either, just a bash
wrapper around a venv entrypoint -- prompt-coach already has the
equivalent via `uv tool install .` (BLUEPRINT.md 10b), it just lacks the
friendly installer script and the wizard.
**Decision**: (1) Standalone binary means an installer script only
(`uv tool install --force .`, which already places `prompt-coach` on
PATH via uv's own tool-install mechanism -- no manual symlinking needed,
Hermes's script predates/doesn't rely on that), not a PyInstaller/Nuitka
compiled executable. A compiled binary is explicitly deferred, to revisit
only if nudge-hook latency (see the entry above) is still a problem after
today's import fix -- it would plausibly help further (no `uv run`
resolve, no cold Python start) but costs a new build pipeline and a
bigger artifact. (2) `prompt-coach setup` (new subcommand) ends by
offering to immediately run `report` or `dash`, chaining into an action
like Hermes's own setup does, rather than exiting silently after writing
config. (3) Store selection is a new opt-in `enabled` list in
`StoresConfig` (default: all four, preserving today's implicit
"everything present is used" behavior for existing users) rather than an
opt-out list -- consistent with the product's core privacy stance of
deliberate inclusion, and means a future fifth store never silently
turns itself on. (4) The wizard uses plain `typer.confirm`/`typer.prompt`
(already available via the existing typer/rich dependencies), not a new
checklist library -- matches the "small lift, no new deps" spirit of
decision (1).
**Why**: matching Hermes's actual, verified setup shape rather than an
assumed one avoids inventing a UX that diverges from the CLI Alistair
already knows and referenced. Deferring the compiled-binary path keeps
today's scope to what was actually asked for and decided, with a
concrete, measured reason to revisit (not a vague "maybe later").
**Affects**: new `install.sh` at repo root; new `setup` command in
cli.py; `config.py` `StoresConfig` gains `enabled` (env
`PROMPT_COACH_ENABLED_STORES`, toml `[stores] enabled`); `default_stores()`
in cli.py filters on it; README.md quick-start section; BLUEPRINT.md new
phase section. Not yet built -- see the task list this session.
**Decided by**: Alistair (binary scope: "installer script only"; wizard
scope: "wizard chains into an action"), remaining implementation details
by Claude (Sonnet 5) per existing project conventions, 2026-07-29.

## 2026-07-30 - Per-directory nudge override folded into the setup scope

**Trigger**: Alistair asked whether nudge could be enabled/disabled per
session or per directory. Checked Claude Code's hooks docs live rather
than assuming: hooks registered at different scopes (global `~/.claude/
settings.json` vs project-level `.claude/settings.json`) merge/accumulate
rather than override, so a project-level settings file cannot cancel a
globally-registered hook -- there is no config-file mechanism to exclude
one project from a global hook. Session-scoped hook control does not
exist at all (no env var, no per-invocation toggle; `UserPromptSubmit`/
`Stop` don't support `matcher` patterns either, confirmed silently
ignored if set). What does exist: both events' payloads include `cwd`.
**Decision**: implement per-directory control inside prompt-coach itself
rather than relying on Claude Code's settings hierarchy, since the
hierarchy can't do it. `NudgeConfig` gains `dir_overrides: dict[str,
str]` (path -> off/coach/always), config.toml-only (`[nudge.
dir_overrides]`, no env var -- it's a mapping, not a scalar, and env vars
don't have a clean way to express one). `nudge.py` resolves the
longest-matching path prefix from the hook's `cwd` before falling back to
`cfg.nudge.mode`. Session-level control is explicitly not built: Claude
Code gives no hook into session identity before a hook fires, and
directory is the closest stable, available proxy. The setup wizard
(2026-07-29 entry) gets one extra optional step -- override nudge for the
directory `setup` is being run from -- rather than a full multi-directory
management UI, keeping the wizard focused per that entry's own scope
line.
**Why**: matches this project's standing practice of checking the actual
platform capability before designing around it (same discipline as the
Stop-hook and block-and-rewrite entries on 2026-07-27) -- Alistair's ask
assumed Claude Code might already support this, and it doesn't, so the
real design question was "where does this belong," not "how do we
configure the built-in version."
**Affects**: config.py (`NudgeConfig.dir_overrides`), nudge.py
(`build_response`'s mode resolution), the setup wizard's scope (one more
optional step), tests. Folded into the not-yet-built task list from the
2026-07-29 entry, not a separate build.
**Decided by**: Alistair ("yes" to folding it into the scoped work),
2026-07-30.

## 2026-07-30 - Claude Code slash command + Hermes nudge equivalent

**Trigger**: Alistair asked for a Claude Code slash command to invoke
prompt-coach for reporting, then asked to check the same for Copilot and
Hermes. Checked each platform's actual current docs rather than assuming
parity across them:
- Claude Code: `.claude/commands/*.md` still works identically to the
  newer `.claude/skills/` format (custom commands were merged into
  skills, old files keep working); `` !`command` `` syntax injects real
  command output into context before Claude responds; `allowed-tools`
  pre-approves it.
- Hermes: its own hooks docs explicitly describe `pre_llm_call` as "the
  UserPromptSubmit equivalent" and its shell hooks accept Claude Code's
  JSON response shapes directly -- a close, documented parallel, not an
  invented one. But `pre_llm_call` can only return `{"context": ...}`; it
  cannot block, so there is no equivalent of Claude Code's block-and-
  rewrite flow available on Hermes at all.
- Copilot Chat: checked the official VS Code docs directly (a subagent's
  first pass cited mostly third-party blog posts, so this one was
  re-verified against code.visualstudio.com itself). Prompt files
  (`.github/prompts/*.prompt.md`) exist but cannot embed shell command
  execution -- confirmed, no `!command` equivalent. A true `/prompt-coach`
  there needs a full VS Code extension (Chat Participant API), not a
  markdown file.
- Codex CLI: not installed as a standalone binary on this machine (VS
  Code extension only, per BLUEPRINT.md 16.4a), so it inherits Copilot's
  constraints -- no separate CLI-level command surface exists to target.
**Decision**: built the two that are actually comparable-effort. (1)
`~/.claude/commands/prompt-coach.md` (global, outside this repo):
`` !`prompt-coach $ARGUMENTS` `` with `allowed-tools: Bash(prompt-coach *)`,
so `/prompt-coach report --since 7d` etc. just works from any project. (2)
`nudge.py`'s `build_response` now dispatches on `hook_event_name`:
`pre_llm_call` payloads (message at `extra.user_message`, not top-level
`prompt`) get a new `_hermes_tip_response()` path -- same calibrated
triggers and once-per-session gate as Claude Code's coach mode, but
always tip-only via context injection, since there's nothing to block.
"always" mode has no meaningful translation for a context-only hook, so
it collapses to coach-style behavior rather than inventing a fake
"unconditional" semantic. The same `prompt-coach nudge` command serves
both platforms -- it was already harness-agnostic (just reads stdin JSON),
so no new CLI command was needed. Copilot/Codex: left as "ask it directly
in chat" -- both already have full tool-calling/terminal access, so
nothing is actually blocked, there's just no slash-command shortcut
available without a much bigger VS Code extension project, which wasn't
asked for.
**Bug found and fixed during live smoke testing**: `_TIP_SCOPE`'s text
said "...before Claude starts", hardcoded to Claude Code even though this
hook now also fires for Hermes sessions running arbitrary models.
Genericized to "before the agent starts".
**Why**: matches the "check the actual platform capability before
designing around it" discipline already established for the earlier
per-directory-nudge and setup-wizard decisions today -- the interesting
finding wasn't "can we build this," it was "these three platforms are not
equivalent, and pretending they are would mean either a broken Hermes
integration (assuming block support that doesn't exist) or an
over-promised Copilot one (assuming injection that doesn't exist)."
**Affects**: nudge.py (`_hermes_tip_response`, `build_response` dispatch,
`_TIP_SCOPE` wording), cli.py (`nudge` docstring), tests (test_nudge.py
`TestHermesPreLlmCall`), README.md (Hermes wiring + slash command
sections), `~/.claude/commands/prompt-coach.md` (new, outside this repo).
240 tests total (up from 231), ruff/black clean.
**Decided by**: Alistair ("build it" on the Hermes side; the Claude Code
command was built directly after "YES"), 2026-07-30.

## 2026-07-30 - Hooks re-enabled, cache backfilled, thresholds checked against real data

**Trigger**: working through the remaining pick-up list items in one pass:
re-enable the nudge hooks, backfill model-fit coverage, and revisit the
first-guess thresholds now that real data exists to check them against.
**Decision/findings**:
- Re-enabled the Claude Code hook in `~/.claude/settings.json`
  (UserPromptSubmit 25s, Stop 10s), pointed at the installed
  `/home/alistair/.local/bin/prompt-coach nudge` rather than `uv run
  --project ...` -- the ~15-20% faster path found during the 2026-07-29
  latency investigation. Verified the exact configured command against a
  synthetic payload before considering it live.
- Wired `~/.hermes/config.yaml`'s `hooks.pre_llm_call` to the same
  binary. Verified via `hermes hooks test` -- which surfaced that the
  test harness's `--payload-file` merges over a *flat* kwargs-shaped
  default payload (`user_message` at top level, matching the Python
  callback signature), not the wrapped `extra.user_message` wire shape
  real firings use -- a harness quirk, not a bug in `_hermes_tip_response`;
  confirmed once the payload was shaped to match. Left Hermes's own
  first-use consent prompt intact rather than pre-approving it in the
  allowlist file myself -- that's a deliberate one-time confirmation
  designed for the user to see, not something to skip on their behalf.
- Live-testing also caught that the installed tool was stale (still
  showed the pre-fix "before Claude starts" wording) -- `uv tool install
  --force .` needs re-running after every source change, which is easy to
  forget; re-ran it and verified the fix landed.
- Backfilled model-fit coverage: `cache clear` + resync took coverage
  from 16/916 to 458/712 classifiable. Real findings: 5 "overpowered"
  (claude-opus-4-8 used on short prompts), 0 "underpowered" (no
  small-tier models used on high-demand prompts in this corpus at all).
- Checked demand/tier thresholds against the real distribution rather
  than guessing whether they needed changing: prompt-length percentiles
  (p75=156, p90=402, p95=1557 chars) put the 200/1200-char low/high
  cutoffs at roughly the 75th and 90-95th percentiles -- reasonable
  population splits, not arbitrary. The 9b/39b tier boundary cleanly
  separates every real local model observed (8b models land small,
  26-35b models land medium, no ambiguous boundary cases). The low
  overall finding rate (5/458 classifiable, ~1%) confirms the system
  isn't noisy despite demand-tier being a crude single-message-length
  proxy -- most individual turns are short regardless of task complexity
  (median ~84 chars), but false positives stay rare because "large-tier
  model usage" is itself rare in this corpus.
- Re-checked the nudge short-vague keyword list against the fresh,
  larger corpus (1376 human prompts, up from 1739 at a different
  snapshot in time): trigger rate is 1.5% now vs. 1.5% at the original
  2026-07-27 calibration. Stable, no drift -- no change made.
**Why recording a "no change needed" outcome**: both thresholds were
flagged on the pick-up list as first-guess, revisit-if-noisy. Verifying
they're fine and writing that down prevents a future session from
re-litigating the same question from scratch with no new information --
same value as recording a change, just the opposite conclusion.
**Affects**: `~/.claude/settings.json`, `~/.hermes/config.yaml` (both
outside this repo), local `~/.cache/prompt-coach/cache.db` (cleared and
resynced). No source code changes -- this entry is a verification record,
not a build.
**Decided by**: Alistair ("all of it"), 2026-07-30.

## 2026-07-31 - Live feedback round: grounded rewrites, machine-payload guard, setup TTY

**Trigger**: Alistair used the live hook outside this repo and reported
three things in one session: (1) the LLM rewrite is generic -- "the prompt
improvement stuff must use the context the 'bad' prompt is running in,
otherwise it's nowhere near as valuable"; (2) the nudge fired on a
harness-injected `<task-notification>` payload (a background-build
completion event) and offered to "tighten" it; (3) `/prompt-coach setup`
died with "Aborted." because the slash command's shell injection has no
TTY for the wizard's interactive prompts.
**Decision**:
- **Grounded rewrites** (`nudge.py gather_context()`): the rewrite call
  now sends a SESSION CONTEXT block -- working directory, an excerpt of
  the nearest project doc (reusing `find_project_docs`/`is_redirect_stub`
  from the docs-quality analysis, redirect stubs skipped), and the last
  few human prompts tailed from the session transcript (generalising the
  Stop hook's `_last_human_prompt` into `_tail_human_prompts`; the
  just-submitted prompt is filtered out by stripped-text comparison since
  parse_line strips content). Caps: 1500 chars of doc, 3 prior prompts at
  300 chars each -- generous enough to ground, small enough to stay well
  inside the 32k-ctx default model with the hook's 20s timeout. Context
  is only gathered when the LLM probe succeeds, so the degraded path pays
  no filesystem cost. Live smoke against the real desktop Ollama: the
  rewrite went from generic filler to naming uv/ruff/black, AGENTS.md,
  the A1-A13 rubric, and the real store paths. It now leans verbose
  (stuffs in doc boilerplate) -- tuning `_REWRITE_SYSTEM` stays on the
  pick-up list, but the grounding itself is what makes it worth tuning.
- **Machine-payload guard** (`_is_machine_prompt`): harness-injected
  payloads arrive through UserPromptSubmit shaped exactly like typed
  prompts. New `_HARNESS_WRAPPED` regex matches the wrapper tags Claude
  Code injects as user turns (`task-notification`, `command-message`,
  `command-name`, `system-reminder`, `local-command-stdout`, `bash-*`),
  plus `classify_origin` from stores/base.py for TASK:/HANDOFF:
  orchestration specs. Guard sits in `_tip_for` (covers coach mode, Stop,
  and Hermes) and at the top of the `always` path (which bypasses
  `_tip_for` by design). A blocked machine payload also no longer burns
  the once-per-session gate. Verified with the exact live payload against
  both `uv run` and the installed binary.
- **setup without a TTY**: wizard body moved to `_setup_wizard()`; the
  `setup` command wraps it and catches click's `Abort` (EOF on any
  question, or Ctrl-C), printing a pointer to run it in a real shell and
  exiting 0 -- consistent with the first-run-isn't-an-error convention.
  Test note: click's CliRunner feeds prompts their defaults on input
  exhaustion instead of raising Abort (the real no-TTY run does abort, as
  the live paste showed), so the test injects Abort at the wizard
  boundary rather than pretending an empty input stream reproduces it.
- Reinstalled via `uv tool install --force .` and re-verified against the
  installed binary -- the stale-installed-tool trap from 2026-07-30 is why
  this is now a standard closing step, not an afterthought.
**Alternatives considered**: putting the harness-wrapper patterns into
stores/base.py's `classify_origin` was rejected for now -- the corpus
readers already filter these via transcript metadata (`promptSource`,
origin kind), so the text-shape guard is a hook-payload concern, not a
corpus one. Widening it later if wrapped payloads ever show up in corpus
stats is cheap.
**Affects**: nudge.py, cli.py, test_nudge.py, test_cli.py. 260 tests (up
from 243).
**Decided by**: Alistair (live feedback), built same session, 2026-07-31.

## 2026-07-31 - _REWRITE_SYSTEM tuned against live output: context is background, not content

**Trigger**: same-day follow-up to the grounded-rewrites build -- the first
live grounded rewrite was a mini-spec that recited project-doc boilerplate
(uv/ruff/black conventions, store paths) alongside the useful specifics.
**Decision**: two additions to `_REWRITE_SYSTEM`, iterated against the
real desktop Ollama rather than guessed: (1) "the context is background
for YOU, not content for the rewrite: never recite, summarize, or restate
the project docs or conventions -- the assistant reading the prompt
already has them"; (2) a length anchor -- "about as long as a well-written
version of the original ask (a few sentences), never a mini-spec".
Verified on both trigger shapes: the long-unshaped prompt-coach ask
dropped from ~9 sentences of doc recitation to 3 grounded sentences; a
short-vague ask in email-client ("just redo the whole settings page")
produced 3 sentences correctly citing that project's `design-pack/` and
`BLUEPRINT.md` section 5. `_block_reason()` wording left as-is -- it reads
fine around the tighter rewrites.
**Affects**: nudge.py (`_REWRITE_SYSTEM` only, no logic change). 260
tests unchanged. Binary reinstalled, smoke residue cleaned.
**Decided by**: follow-through on the same-session pick-up item, 2026-07-31.

## 2026-08-07 - Accept/reject slash commands for nudge blocks: buttons aren't possible, slash commands are the closest thing

**Trigger**: Alistair asked for a button on the nudge block-and-rewrite
flow -- one to accept the rewrite, one to send the original as-is. Pure
quality-of-life: pasting the rewrite back in by hand was friction on
every block.
**Decision**: Checked live against current Claude Code hook docs (not
assumed): `UserPromptSubmit`'s output schema has no interactive-choice
mechanism -- only `block`/`continue` plus `reason`/`systemMessage`/
`additionalContext` text fields. The `"ask"` decision that produces real
Yes/No/Always buttons is `PreToolUse`-only, not available here. Closest
real equivalent: custom slash commands -- discoverable via tab-complete,
no free-text guessing, same `!`command`` substitution pattern the
existing `~/.claude/commands/prompt-coach.md` already uses, and
`${CLAUDE_SESSION_ID}` is available as a substitution variable to key
state by session. Shipped: when a block offers a rewrite,
`_save_pending_rewrite` stashes `{original, rewritten}` keyed by session
ID in a new `nudge_pending.json` (bounded like the existing
nudged-sessions state, oldest dropped past 500). Two new global slash
commands, `~/.claude/commands/coach-accept.md` and `coach-original.md`
(not part of this repo -- it's Claude Code user config, same as
`prompt-coach.md`), run `prompt-coach nudge-consume --want
{rewritten,original} --session "${CLAUDE_SESSION_ID}"` and resend the
output as the next message. Popping the pending entry also sets a
one-shot `skip_next` flag, checked at the top of `build_response`'s
prompt path, so the resend isn't immediately re-blocked -- needed for
`always` mode, which has no once-per-session gate to fall back on.
**Alternatives considered**: a bare y/n reply recognized on the next
`UserPromptSubmit` -- rejected, since it collides with any real
one-letter prompt and isn't discoverable the way a tab-completed slash
command is.
**Affects**: nudge.py (pending-state helpers, `_block_reason` wording,
skip check in `build_response`), cli.py (`nudge-consume`), README.md,
test_nudge.py, test_cli.py. 269 tests (up from 260). Two new files
outside this repo: `~/.claude/commands/coach-accept.md`,
`coach-original.md`.
**Decided by**: Alistair, built same session, 2026-08-07. Reinstalled
via `uv tool install --force .` and verified live -- a real block
against the desktop Ollama model, then `nudge-consume` for both
`rewritten` and `original` -- against the installed binary, not just
`uv run`.
