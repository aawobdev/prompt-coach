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
