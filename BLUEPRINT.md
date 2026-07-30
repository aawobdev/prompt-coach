================================================================
PROJECT BLUEPRINT
================================================================
Generated: 2026-07-20 (v2, supersedes 2026-07-20 v1 by deepseek-v4-flash)
Architect: Claude (Fable 5), Claude Code session, with live-system inspection
Project:   prompt-coach: local-first personal prompt coach
Platforms: CLI (Python 3.12, Linux/WSL2)
Status:    READY FOR EXECUTION

ACTIVE ROLES:
  [x] Architect  (this document; interview + inspection complete)
  [x] Developer  (Claude Code executes all build tasks; user decision 2026-07-20)
  [x] Tester     (Claude Code; test tasks and the final gate)
  [ ] Designer   (N/A: CLI text and markdown output only, no visual surface)
  [ ] DevOps     (N/A: local tool, nothing deployed; install is `uv sync`)
  [ ] Security   (N/A as a separate audit phase: single-user, local, read-only
                  tool; security requirements are captured in section 4d and
                  verified in the final gate instead)
  [ ] End-User   (N/A phase 1: the primary user supervises the build directly)
================================================================

> v1 of this blueprint was produced without inspecting the live systems and
> invented the Hermes store path and schema. v2 corrects every store fact
> against the real machines, restructures the document to the
> `blueprint-orchestration` skill, and applies `prompting-standards` Part A
> to every build task. All decision history is in `DECISIONS.md`.

---

### 0. COVERAGE MATRIX

One platform column (CLI on Linux/WSL2). Every concern is covered or
explicitly N/A with a reason.

| Concern | CLI | Covered by |
|---------|-----|-----------|
| Design / UX | N/A: text output; report layout spec'd in 4 and T27 | - |
| System design & architecture | yes | Architect, section 3 |
| Stack & hosting selection | yes | section 3b |
| Development | yes | Developer, section 6 (T5-T31, T33) |
| Functional testing | yes | Tester, section 6 (T10, T12, T15, T17, T20, T22, T24, T26, T29, T32, T34) |
| Non-functional (perf) | yes | section 4c, verified in T34 |
| Accessibility | N/A: terminal text; no color-only signalling in output | - |
| Security (design) | yes | section 4d |
| Data & privacy | yes | section 4e (this is the product's core promise) |
| Deployment & release | N/A: nothing deployed; runs from the repo via uv | - |
| Observability | N/A: interactive CLI; errors surface directly to the user | - |
| End-user validation | yes | T34 live smoke run by the primary user |
| Documentation | yes | section 10d (T1-T4, T33) |

---

### 1. PRODUCT BRIEF

prompt-coach is a local-first CLI that reads your own prompt history from the
session stores already on your machine (Hermes `state.db`, Claude Code project
transcripts), analyses your personal prompting style, and produces a coaching
report: style metrics, scores against your own `prompting-standards` rubric
(A1-A13), and LLM-detected patterns, strengths, and growth areas. Analysis
runs against a local Ollama model only; prompt content never leaves the
machine. It is not a prompt optimizer (it coaches the prompter, not the
prompt), not an observability dashboard (llm-api owns tokens/cost), and not a
SaaS. Done means: `prompt-coach report` produces a useful, honest coaching
briefing over the real corpora, degrading gracefully to deterministic-only
analysis when no local model is reachable.

Why it can exist at all: the gap between prompt optimizers (DSPy, promptfoo),
observability tools (Langfuse, Helicone), and population research is a tool
that studies the individual prompter. Nobody uploads their full prompt history
to a SaaS, so the niche is only solvable local-first. The primary user already
has the data (62 Hermes sessions, 108 Claude Code transcripts) and the local
inference hardware.

**Users**: primary is Alistair (power user: Hermes + Claude Code + llm-api +
Ollama). Secondary is anyone with a local LLM and a supported session store.

---

### 2. USER STORIES

**Happy path**
As a power user, I want to run `prompt-coach report --since 30d`, so that I
get a coaching briefing over my recent Hermes and Claude Code history.
Acceptance criteria:
- Report contains: summary counts, style metrics table, rubric scorecard
  (human and machine segments side by side), strengths / growth areas /
  notable patterns, topic distribution.
- All LLM calls went to the configured local endpoint only.
- Second run within the same data window completes fast (LLM cache hits).

**Edge case: no local model**
As a user whose desktop Ollama is off, I want `prompt-coach report` to still
work, so that deterministic analysis is never blocked by the GPU box.
Acceptance criteria:
- Report renders with metrics + deterministic rubric checks.
- A visible banner states: LLM unavailable, deterministic analysis only.
- Exit code 0.

**Failure case: store missing or locked**
As a user on a machine without Hermes, I want commands to succeed on the
stores that do exist, so that one absent store never aborts the run.
Acceptance criteria:
- `discover` lists each store with available/unavailable and a reason.
- `report` proceeds with the remaining stores and says what was skipped.
- A read-locked or corrupt store is reported, not fatal.

---

### 3. ARCHITECTURE

```
+------------------------------------------------------------------+
|                          prompt-coach                            |
|                                                                  |
|  stores/               cache.py               analysis/          |
|  +--------------+     +----------------+     +----------------+  |
|  | hermes.py    |     | cache.db       |     | metrics.py     |  |
|  | claude_code  | --> |  prompts       | --> | rubric.py      |  |
|  | json_import  |     |  file_state    |     | patterns.py    |  |
|  +--------------+     |  prompts_fts   |     +-------+--------+  |
|   read-only, streamed |  llm_cache     |             |           |
|                       +-------+--------+             v           |
|                               |              +----------------+  |
|                               |              | report/        |  |
|                               +------------> | query.py       |  |
|                                              | cli.py         |  |
|                                              +----------------+  |
+------------------------------------------------------------------+
        |                                        |
        v                                        v
+-------------------+                  +----------------------+
| Session stores    |                  | Local LLM only       |
| ~/.hermes/state.db|                  | Ollama 192.168.1.123 |
| ~/.claude/projects|                  | :11434/v1 (or llm-api|
| JSON imports      |                  | gateway, guarded)    |
+-------------------+                  +----------------------+
```

**Data flow**
1. `sync` (implicit in every command, explicit via `prompt-coach cache sync`)
   streams prompts from each store into the local cache DB, incrementally.
2. Deterministic analysis (style metrics + rubric rule checks) runs over the
   full cached corpus. Cheap, no LLM.
3. LLM analysis (rubric judging, pattern map-reduce) runs over stratified
   samples, every call cached in `llm_cache`.
4. `report` renders markdown; `query` retrieves via cache FTS5 then asks the
   local LLM to answer with citations.

**Key design decisions** (full reasoning in DECISIONS.md)
- Prompts only, never assistant responses: the coaching signal is what the
  user types; excluding responses saves tokens and shrinks the privacy
  surface.
- Human/machine segmentation at extraction time: Hermes "user" messages
  include machine-generated `hermes -z` task specs, and those are scored as
  their own segment (they audit the orchestration pipeline's prompt quality
  against prompting-standards Part A). The human segment is primary.
- Local LLM only, with a guard: the client refuses non-private base URLs
  unless explicitly configured otherwise (section 4e).
- Cache DB as the keystone: parse each store once, resync incrementally,
  dedupe session forks, index for FTS, memoise LLM results.

---

### 3b. STACK & HOSTING SELECTION

Stack is mandated by the existing scaffold and house conventions (Python 3.12,
uv, typer, src-layout, ruff/black/pytest, mirroring llm-api). Hosting is N/A
(local CLI). Two real decisions remained; both evaluated, both recorded in
DECISIONS.md:

**Decision 1: synchronous code, stdlib `sqlite3`, no ORM.**
This is a linear batch pipeline (read, cache, analyse, render) with no
concurrent I/O worth hiding: SQLite reads are local, JSONL parsing is
disk/CPU-bound streaming, and the LLM bottleneck is a single local GPU that
serialises requests anyway. SQLAlchemy would put an ORM over four read-only
SELECTs against a schema this project does not own; aiosqlite would drag an
event loop and pytest-asyncio through every module for zero throughput.
Consequence: `sqlalchemy`, `aiosqlite`, `pytest-asyncio` are removed from
pyproject (T5). Reversibility: a ThreadPoolExecutor around LLM calls is a
contained later change if concurrency ever pays.

**Decision 2: runtime analysis model is local-only, direct Ollama default.**
Default `http://192.168.1.123:11434/v1`, model `qwen3-coder-30b:latest` (num_ctx 32768 baked in; the base tag runs at the 4k server default). The llm-api
gateway (`http://localhost:8080/v1`) is supported but guarded, because the
gateway can route or fall back to cloud providers, which would silently break
the privacy promise (section 4e). Config precedence: `PROMPT_COACH_*` env
vars, then `~/.config/prompt-coach/config.toml`, then defaults.

---

### 4. TECHNICAL SPEC

**Runtime**: Python >= 3.12, uv-managed venv.
**Dependencies** (after T5 prune): typer, openai (sync client), httpx, jinja2,
pydantic. Dev: pytest, pytest-cov, ruff, black, respx.

#### 4.1 File structure

```
prompt-coach/
├── AGENTS.md                    canonical agent guidance
├── BLUEPRINT.md                 this document
├── STATUS.md                    task/phase tracking (T3)
├── DECISIONS.md                 decision log (T4)
├── README.md                    user-facing quick start (T33)
├── pyproject.toml
├── src/prompt_coach/
│   ├── __init__.py
│   ├── __main__.py              python -m prompt_coach
│   ├── cli.py                   typer app: discover, stats, report, query,
│   │                            import, cache (T31)
│   ├── config.py                env > toml > defaults (T7)
│   ├── models.py                Prompt, SourceKind, PromptOrigin,
│   │                            StyleMetrics, RuleScore, RubricSummary,
│   │                            PatternReport, ReportData (T6)
│   ├── cache.py                 CacheDB: sync, dedupe, FTS5, llm_cache (T16)
│   ├── query.py                 FTS retrieve + LLM answer (T30)
│   ├── stores/
│   │   ├── base.py              SessionStore protocol, StoreInfo (T8)
│   │   ├── hermes.py            state.db reader (T9)
│   │   ├── claude_code.py       JSONL streaming reader (T11)
│   │   └── json_import.py       generic JSON import (T13)
│   ├── analysis/
│   │   ├── metrics.py           deterministic style metrics (T21)
│   │   ├── rubric.py            A1-A13 scoring (T23)
│   │   └── patterns.py          LLM pattern map-reduce, incl. topics (T25)
│   ├── llm/
│   │   ├── client.py            LocalLLM, guard, LLMUnavailable (T18)
│   │   └── prompts.py           versioned templates (T19)
│   ├── report/
│   │   ├── generator.py         build_report (T28)
│   │   └── templates/report.md.j2  (T27)
│   └── server/                  empty; read-only HTTP API is phase 2
└── tests/                       one test module per source module + fixtures
```

`analysis/topics.py` and `analysis/trends.py` from v1 are gone: topic
distribution is one output field of the pattern map-reduce (a per-session
topic pass over thousands of prompts is bulk LLM work that prompting-standards
B7 forbids), and trend analysis is phase 2.

#### 4.2 Store: Hermes (verified against the live DB 2026-07-20)

Path: `~/.hermes/state.db` (SQLite, WAL). Open read-only via URI
`file:...?mode=ro`. There is no `profiles` table (v1 invented it).

Columns actually used:
- `sessions(id TEXT PK, source TEXT, model TEXT, started_at REAL unixepoch,
  ended_at REAL, title TEXT, message_count INT, cwd TEXT, git_repo_root TEXT,
  archived INT)`
- `messages(id INTEGER PK, session_id TEXT, role TEXT, content TEXT,
  timestamp REAL unixepoch, active INT, compacted INT)`

Extraction filter: `role='user' AND active=1 AND compacted=0` (all 281
current user rows have compacted=0; the filter is defensive against future
compaction summaries). Origin classification: content matching machine task
spec shapes (`TASK:` / `Task spec:` prefixes and similar) is `machine`, the
rest `human`. FTS5 tables (`messages_fts`) exist in the DB but are not used;
the unified cache FTS covers retrieval (DECISIONS.md).

#### 4.3 Store: Claude Code (verified against live transcripts 2026-07-20)

Path: `~/.claude/projects/<project-slug>/<session-uuid>.jsonl`. 108 main
transcripts (536 further subagent transcripts nest deeper under
`<session>/subagents/` and are excluded by design: they are machine traffic,
the file-level counterpart of the isSidechain filter). Total tree 691MB,
691MB. Each line is a JSON object. A hand-typed user prompt line looks like:

```json
{"type":"user","message":{"role":"user","content":"..."},
 "promptSource":"typed","origin":{"kind":"human"},"isSidechain":false,
 "uuid":"...","timestamp":"2026-06-22T21:39:40.265Z","sessionId":"...",
 "cwd":"/home/alistair/projects/x","gitBranch":"master"}
```

Accept a line only when ALL hold:
1. `type == "user"`
2. `origin.kind == "human"` (drops tool results, hooks, machine entries)
3. `promptSource == "typed"`
4. `isSidechain` is falsy (drops subagent traffic)
5. content, after normalisation, is a real prompt: strings pass through;
   content-block arrays keep only `text` blocks; lines whose content is a
   local-command echo (`<command-name>`, `<local-command-stdout>`) or a bare
   `<system-reminder>` wrapper are dropped, and inline system-reminder blocks
   are stripped from otherwise-real prompts.

Files are streamed line by line, never loaded whole. Incremental resync
records `(path, mtime, size, byte_offset)`; JSONL files are append-only per
file, so a grown file resumes from its stored offset, and a shrunk/rewritten
file re-parses from zero (upserts are idempotent). Session forks copy earlier
messages into new files: dedupe on message `uuid` when present, else on
`(content_hash, timestamp)`.

#### 4.4 Store: JSON import

`prompt-coach import --file x.json`: a list of sessions
`{session_id, title, timestamp, messages:[{role, content}]}` (v1 format kept).
User messages become prompts with `origin=human` unless the machine
classifier says otherwise.

#### 4.5 Cache DB

`$XDG_CACHE_HOME/prompt-coach/cache.db` (default `~/.cache/prompt-coach/`),
created with mode 0600. Tables:
- `prompts(source, session_id, message_ref, content, content_hash, timestamp,
  origin, cwd, git_repo, PRIMARY KEY(source, session_id, message_ref))` plus
  a uniqueness guard on `(content_hash, timestamp)` for fork dedupe
- `file_state(path PK, mtime, size, offset)`
- `llm_cache(key PK, payload JSON, model, template_version, created_at)`,
  key = sha256(sorted content hashes + template version + model)
- `prompts_fts` FTS5 external-content index over `content`

Deleting the directory erases every derived artifact (rollback and the
privacy retention story in one).

#### 4.6 Key interfaces

```python
class SourceKind(StrEnum): HERMES; CLAUDE_CODE; JSON_IMPORT
class PromptOrigin(StrEnum): HUMAN; MACHINE

@dataclass(frozen=True)
class Prompt:
    source: SourceKind; session_id: str; message_ref: str
    content: str; content_hash: str; timestamp: datetime  # always UTC-aware
    origin: PromptOrigin; cwd: str | None; git_repo: str | None

class SessionStore(Protocol):
    kind: SourceKind
    def discover(self) -> StoreInfo: ...
    def iter_prompts(self, since: datetime | None = None) -> Iterator[Prompt]: ...

class CacheDB:
    def sync(self, stores, force=False) -> SyncStats: ...
    def prompts(self, *, since=None, origin=None, source=None, limit=None): ...
    def search(self, query, limit=20) -> list[Prompt]: ...
    def get_llm(self, key) -> dict | None: ...
    def put_llm(self, key, payload) -> None: ...

class LocalLLM:
    def __init__(self, base_url, model, api_key="ollama", timeout=120.0,
                 allow_remote=False): ...   # raises on public URL unless allowed
    def available(self) -> bool: ...        # GET {base}/models, 2s timeout
    def complete_json(self, system, user, *, temperature=0.0,
                      max_tokens=2000) -> dict: ...  # one re-prompt on bad JSON
```

Timestamps: Hermes REAL unixepoch and Claude ISO-8601 both normalise to
UTC-aware `datetime` at parse time, because `--since` filtering compares
across stores.

**AGENTS.md** at the repo root stays canonical for agents; no CLAUDE.md,
.cursorrules, or copilot files beyond one-line pointers.

---

### 4b. CONTENT INVENTORY

The "content" is the user's existing prompt history; acquisition (cache sync)
is early in the build plan (T8-T17) per the skill.

| Corpus | Location | Volume | Notes |
|--------|----------|--------|-------|
| Hermes | ~/.hermes/state.db | 62 sessions, 281 user msgs (2026-06-12 to 2026-07-17) | many are machine `TASK:` specs; segment |
| Claude Code | ~/.claude/projects/ | 108 main JSONL transcripts (~1,351 prompts); 691MB tree incl. excluded subagent files | the primary corpus; needs the 5-filter parse |
| JSON import | user-supplied | ad hoc | v1 format kept |
| OpenWebUI | ollama VM, remote DB | deferred | phase 2 (DECISIONS.md) |
| llm-api usage logs | llm-api Postgres | deferred | phase 2 enrichment |

Discard list: assistant/tool messages, sidechain traffic, command echoes,
system reminders, inactive/compacted rows.

---

### 4c. NON-FUNCTIONAL REQUIREMENTS

Concrete targets; T34 verifies them on the real corpus.

- Initial full sync of the Claude Code corpus: < 5 minutes on WSL2
  (streaming line reads, no whole-file loads).
- Incremental resync: < 30 seconds when little changed.
- `report --no-llm` over the full cache: < 60 seconds.
- Memory ceiling: < 500MB (iterators end to end; never materialise 691MB).
- Each LLM call payload: <= ~8k estimated tokens (chars/4 heuristic);
  individual prompts truncated to 1,500 chars before inclusion. Conservative
  because the Ollama server's effective num_ctx is unverified (section 9).
- LLM stage on defaults (150-prompt rubric sample + 300-prompt pattern
  sample): completes in one sitting on the RTX 3090 Ti; every call cached so
  re-runs are incremental.

---

### 4d. SECURITY REQUIREMENTS

- All source stores opened strictly read-only: SQLite via
  `file:...?mode=ro` URI (not `immutable=1`: Hermes runs WAL and may write
  concurrently); JSONL opened for reading only. No code path writes to any
  source store.
- Cache directory and DB created 0600/0700; it contains prompt content.
- No secrets in the repo. The only credential the config may hold is a
  gateway key for llm-api on localhost; never a cloud provider key.
- Untrusted input surfaces: JSONL/JSON parsing (malformed lines are skipped
  and counted, never crash the sync); FTS queries built with parameter
  binding, no string-interpolated SQL anywhere.
- ruff security rules (`S`) stay enabled.
- Threat classes out of scope by design: no network listener in phase 1
  (serve is deferred), no multi-user concerns, no untrusted third-party data.

---

### 4e. DATA & PRIVACY

This section is the product. The moat is "your prompt history never leaves
your machine"; every decision below defends it.

- **PII inventory**: prompt content is treated as PII-adjacent in bulk (it
  contains project details, health context, personal habits). Stored only in
  the local cache DB, derived from stores the user already has.
- **Egress policy**: prompt content is sent to exactly one place: the
  configured analysis endpoint. `LocalLLM` refuses any base URL that is not
  localhost or RFC1918 unless `allow_remote = true` is set explicitly in
  config. This exists because the llm-api gateway can fall back to
  OpenRouter: pointing prompt-coach at a gateway is only safe if that
  gateway's routing is pinned local, and the guard makes the user say so out
  loud. The default (direct desktop Ollama at 192.168.1.123) is plain HTTP
  on the LAN; acceptable in this homelab, stated here so it is a choice.
- **Logging**: prompt content is never written to logs, stdout diagnostics,
  or error messages. Counts and hashes only.
- **Retention & erasure**: `rm -rf ~/.cache/prompt-coach/` removes every
  derived artifact. Source stores are never modified.
- **Config hygiene**: config.toml holds no cloud API keys.

---

### 5. DESIGN SPEC

N/A: no visual surface. The report's information design (section order,
tables, segment side-by-side) is fixed in the T27 template contract.

---

### 6. BUILD PLAN

All tasks: Model = Claude Code (user decision: no local-model routing for the
build). Sampling = temp 0 equivalent for all code tasks. Common escalation
rule: if a task needs a fact not in this blueprint (a schema column, a JSONL
field, a path), stop and check the live system or ask; do not invent.
Common self-check (A10): after writing a file, re-read it; run the Verify
command before marking the task done in STATUS.md.

Docs tasks first (they are the contract the code tasks execute against), then
content acquisition early (stores + cache), then analysis, then surface.

---

**T1: Rewrite BLUEPRINT.md (this document)**
- Role: Architect · Reasoning: think
- Description: replace v1 with this structure, correcting all store facts,
  because every downstream task treats the blueprint as ground truth and v1's
  invented schema would propagate into code.
- Input: blueprint-orchestration + prompting-standards skills; verified facts.
- Output contract: this file, all mandatory sections present or N/A'd.
- Verify: `! grep -q $'\u2014' BLUEPRINT.md && grep -q 'state.db' BLUEPRINT.md
  && ! grep -q 'data/sessions[.]db' BLUEPRINT.md`

**T2: Correct AGENTS.md**
- Role: Architect · Reasoning: no_think
- Description: fix store path/schema facts, remove sqlalchemy/aiosqlite/
  FastAPI phase-1 claims and the `--profile` implication, purge em dashes,
  because agents read AGENTS.md before this blueprint and stale facts there
  defeat the correction.
- Output contract: updated AGENTS.md only.
- Verify: `! grep -q $'\u2014' AGENTS.md && grep -q '.hermes/state.db'
  AGENTS.md && ! grep -qi aiosqlite AGENTS.md`

**T3: Create STATUS.md**
- Role: Architect · Reasoning: no_think
- Description: task table T1-T34 all pending, phase summary, blockers section,
  llm-api house format, so every role records progress in one place.
- Output contract: STATUS.md only. Plain English, no section-number
  cross-references.
- Verify: `test $(grep -c 'T[0-9]' STATUS.md) -ge 34`

**T4: Create DECISIONS.md**
- Role: Architect · Reasoning: no_think
- Description: seed with the six decisions of record (store correction;
  phase-1 scope; sync + stdlib sqlite3; Claude executes all; local-only LLM
  guard; machine-prompt segmentation), each with trigger/decision/why/
  affects/decided-by, so future sessions inherit the reasoning, not just the
  outcome.
- Output contract: DECISIONS.md only. Plain English in Affects fields.
- Verify: `test $(grep -c '^## 2026' DECISIONS.md) -ge 6`

**T5: Prune pyproject.toml**
- Role: Developer · Reasoning: no_think
- Description: remove sqlalchemy, aiosqlite, pytest-asyncio and the
  asyncio_mode pytest setting (decision: sync + stdlib sqlite3), because dead
  heavyweight deps invite accidental use and slow `uv sync`.
- Output contract: pyproject.toml only.
- Verify: `uv sync && uv run prompt-coach --help && ! uv run python -c
  'import sqlalchemy' 2>/dev/null`

**T6: models.py**
- Role: Developer · Reasoning: no_think
- Description: the shared dataclasses/enums from section 4.6 (Prompt,
  SourceKind, PromptOrigin, StoreInfo, SyncStats, StyleMetrics, RuleScore,
  RubricSummary, PatternReport, ReportData), because every other module
  imports its types from here and nowhere else.
- Output contract: src/prompt_coach/models.py only.
- Verify: `uv run python -c "from prompt_coach.models import Prompt,
  PromptOrigin, SourceKind, StyleMetrics"`

**T7: config.py**
- Role: Developer · Reasoning: no_think
- Description: load order env `PROMPT_COACH_*` > `~/.config/prompt-coach/
  config.toml` (tomllib) > defaults (base_url http://192.168.1.123:11434/v1,
  model qwen3-coder-30b:latest, allow_remote false, cache dir XDG), returning a
  frozen Config object, because every entry point needs one canonical config.
- Output contract: src/prompt_coach/config.py only.
- Verify: `uv run python -c "from prompt_coach.config import load_config;
  c=load_config(); print(c.llm.base_url, c.llm.model)"` prints the defaults.

**T8: stores/base.py**
- Role: Developer · Reasoning: no_think
- Description: SessionStore Protocol + shared helpers (machine-classifier
  regex lives here so hermes and json_import share it), per section 4.6.
- Output contract: src/prompt_coach/stores/base.py only.
- Verify: `uv run python -c "from prompt_coach.stores.base import
  SessionStore, classify_origin; assert
  classify_origin('TASK: build X').value=='machine'"`

**T9: stores/hermes.py**
- Role: Developer · Reasoning: think
- Description: read-only reader per section 4.2 (mode=ro URI, active=1,
  compacted=0, unixepoch to UTC, origin classification), returning
  StoreInfo(unavailable, reason) rather than raising when the DB is absent
  or locked, because report must survive missing stores.
- Input: T6, T8.
- Output contract: src/prompt_coach/stores/hermes.py only.
- Verify: `uv run python -c "from prompt_coach.stores.hermes import
  HermesStore; i=HermesStore().discover(); print(i)"` shows available=True
  and >= 62 sessions against the live DB, with no write to it.
- Escalate if: live schema differs from section 4.2.

**T10: tests/test_stores_hermes.py**
- Role: Tester · Reasoning: no_think
- Description: build a minimal state.db clone in tmp_path in-test (no binary
  fixture files, so the schema under test is visible in the test source);
  cover: user rows extracted, assistant/tool rows excluded, active=0 and
  compacted=1 excluded, `TASK:` row classified machine, absent DB gives
  available=False.
- Output contract: tests/test_stores_hermes.py only.
- Verify: `uv run pytest tests/test_stores_hermes.py -q`

**T11: stores/claude_code.py**
- Role: Developer · Reasoning: think
- Description: streaming JSONL reader per section 4.3: iter_files over
  ~/.claude/projects, iter_file yielding (byte_offset_after_line, Prompt)
  to power incremental resync, `parse_line` implementing the five filters
  exactly, malformed lines skipped and counted. This is the largest corpus
  and the trickiest filter set; the filters are the spec, invent nothing.
- Input: T6, T8.
- Output contract: src/prompt_coach/stores/claude_code.py only.
- Verify: `uv run python -c "from prompt_coach.stores.claude_code import
  ClaudeCodeStore; s=ClaudeCodeStore(); f=next(s.iter_files());
  print(sum(1 for _ in s.iter_file(f)))"` runs clean on a real transcript.
- Escalate if: a live transcript contains a user line shape the five filters
  cannot classify confidently.

**T12: tests/test_stores_claude_code.py**
- Role: Tester · Reasoning: no_think
- Description: inline tmp_path JSONL fixtures covering: typed human kept;
  sidechain, non-human origin, non-typed, command echo, bare system-reminder
  all rejected; content-block array flattened; inline system-reminder
  stripped; malformed line skipped; offset resume yields only new lines.
- Output contract: tests/test_stores_claude_code.py only.
- Verify: `uv run pytest tests/test_stores_claude_code.py -q`

**T13: stores/json_import.py**
- Role: Developer · Reasoning: no_think
- Description: v1 JSON format reader (section 4.4) mapping user messages to
  Prompts with machine classification via the shared classifier.
- Output contract: src/prompt_coach/stores/json_import.py only.
- Verify: `uv run python -c` round-trip on an inline dict (one session, two
  user messages, one `TASK:` message classified machine).

**T14: Populate tests/fixtures/sample_sessions.json**
- Role: Tester · Reasoning: no_think
- Description: 6+ sessions with deliberately varied styles (vague one-liner;
  explicit prompt with output contract and example; long unstructured wall;
  iterative refinement chain; machine TASK spec; structured-output request),
  because metrics/rubric tests need known-answer inputs.
- Output contract: tests/fixtures/sample_sessions.json only (currently []).
- Verify: `uv run python -c "import json; d=json.load(open(
  'tests/fixtures/sample_sessions.json')); assert len(d)>=6"`

**T15: tests/test_stores_json.py**
- Role: Tester · Reasoning: no_think
- Output contract: tests/test_stores_json.py only, driven by the T14 fixture.
- Verify: `uv run pytest tests/test_stores_json.py -q`

**T16: cache.py**
- Role: Developer · Reasoning: think
- Description: CacheDB per sections 4.5/4.6: schema creation (0600),
  incremental sync using file_state offsets for claude-code and max-timestamp
  watermark for hermes, fork dedupe, FTS5 external-content index kept in
  sync, llm_cache get/put, prompt query API. Keystone module; everything
  downstream reads through it.
- Input: T6, T8 (T9/T11 interfaces; sync accepts any SessionStore).
- Output contract: src/prompt_coach/cache.py only.
- Verify: `uv run python -c` script: temp-dir CacheDB, sync a fake in-memory
  store twice, assert counts stable (idempotent) and FTS search hits.

**T17: tests/test_cache.py**
- Role: Tester · Reasoning: no_think
- Description: idempotent resync, offset resume after file growth, rewritten
  file re-parse, fork dedupe by uuid and by (hash, timestamp), llm_cache
  round-trip and template-version invalidation, FTS search, since/origin/
  source filters.
- Output contract: tests/test_cache.py only.
- Verify: `uv run pytest tests/test_cache.py -q`

**T18: llm/client.py**
- Role: Developer · Reasoning: think
- Description: LocalLLM per section 4.6 on the sync openai client:
  constructor guard raising on non-private base_url without allow_remote
  (privacy requirement 4e); available() via GET models with 2s timeout;
  complete_json parsing/validating and re-prompting once with the parse
  error appended (prompting-standards B5), then raising LLMUnavailable.
- Input: T7.
- Output contract: src/prompt_coach/llm/client.py only.
- Verify: `uv run python -c` asserting the guard raises for
  https://api.openai.com/v1 and available() returns a bool without raising
  when the desktop is offline.

**T19: llm/prompts.py**
- Role: Developer · Reasoning: think
- Description: versioned templates RUBRIC_JUDGE_V1, PATTERN_MAP_V1,
  PATTERN_REDUCE_V1, QUERY_ANSWER_V1. Each: task instruction up top, fenced
  data in the middle, JSON output contract restated at the end (A11), a
  worked example of the expected JSON, and "answer only from the provided
  prompts; say cannot-determine rather than invent" grounding (A8). Template
  version strings feed the llm_cache key.
- Output contract: src/prompt_coach/llm/prompts.py only.
- Verify: `uv run python -c` formats each template with sample args and
  checks the version constants exist.

**T20: tests/test_llm_client.py**
- Role: Tester · Reasoning: no_think
- Description: respx-mocked: happy JSON; malformed JSON then valid on
  re-prompt (asserts exactly two calls); timeout raises LLMUnavailable;
  guard accepts localhost/192.168.x, rejects public hosts, allows with
  allow_remote=True.
- Output contract: tests/test_llm_client.py only.
- Verify: `uv run pytest tests/test_llm_client.py -q`

**T21: analysis/metrics.py**
- Role: Developer · Reasoning: think
- Description: deterministic StyleMetrics per segment (human/machine/all):
  avg/median estimated tokens (chars/4), prompts per session, refinement
  rate (sessions with >1 user prompt), example rate, constraint rate,
  structured-output rate, first-message content ratio. Pure functions,
  no I/O, exact and repeatable (this is the "vs baseline" anchor for
  phase-2 trends).
- Input: T6.
- Output contract: src/prompt_coach/analysis/metrics.py only.
- Verify: `uv run python -c` on three hand-built prompts asserting exact
  values.

**T22: tests/test_metrics.py**
- Role: Tester · Reasoning: no_think
- Output contract: tests/test_metrics.py only, using the T14 fixture via the
  json_import store for realistic shapes plus hand-built edge cases (empty
  corpus, single prompt, all-machine corpus).
- Verify: `uv run pytest tests/test_metrics.py -q`

**T23: analysis/rubric.py**
- Role: Developer · Reasoning: think
- Description: A1-A13 scoring. Deterministic checks corpus-wide (A4
  structure, A5 output-contract markers, A6 example presence, A11 length
  placement). LLM judge (RUBRIC_JUDGE_V1, 5 prompts/call, 1,500-char
  truncation) on a seeded stratified sample for judgement rules (A1, A2, A3,
  A8). APPLICABILITY map: A7/A12/A13 N/A except orchestration-shaped
  prompts; report states per-rule coverage so N/A is visible, never a silent
  zero. aggregate() produces per-rule means + best/worst exemplar refs,
  split human vs machine (the machine segment audits the orchestration
  pipeline's own task authoring).
- Input: T6, T16, T18, T19.
- Output contract: src/prompt_coach/analysis/rubric.py only.
- Verify: `uv run python -c` scoring one strong and one weak prompt
  deterministically; strong outscores weak on A4/A5.
- Escalate if: a rule cannot be honestly scored from prompt text alone:
  mark it N/A in APPLICABILITY with a comment, do not fake a heuristic.

**T24: tests/test_rubric.py**
- Role: Tester · Reasoning: no_think
- Description: applicability map, deterministic scores on known prompts,
  mocked-LLM judge merge, aggregation by origin, sample determinism by seed.
- Output contract: tests/test_rubric.py only.
- Verify: `uv run pytest tests/test_rubric.py -q`

**T25: analysis/patterns.py**
- Role: Developer · Reasoning: think
- Description: detect_patterns(prompts, llm, cache, sample_size=300,
  batch_size=25): seeded stratified sample (source x origin x recency);
  map = PATTERN_MAP_V1 per batch (topics, habits, weaknesses, one exemplar
  each); reduce = PATTERN_REDUCE_V1 over digests into strengths / growth
  areas / notable patterns / topic distribution. Every call goes through
  llm_cache. Payloads capped ~8k estimated tokens.
- Input: T6, T16, T18, T19.
- Output contract: src/prompt_coach/analysis/patterns.py only.
- Verify: `uv run python -c` with a stub LLM asserting map call count ==
  ceil(sample/batch) and reduce output shape.
- Escalate if: desktop Ollama's effective num_ctx proves smaller than the 8k
  payload budget; shrink batch_size, record in DECISIONS.md.

**T26: tests/test_patterns.py**
- Role: Tester · Reasoning: no_think
- Description: sampling determinism with seed; caching short-circuits repeat
  calls (stub LLM call counter); graceful path when llm unavailable.
- Output contract: tests/test_patterns.py only.
- Verify: `uv run pytest tests/test_patterns.py -q`

**T27: report/templates/report.md.j2**
- Role: Developer · Reasoning: no_think
- Description: sections in order: Summary; Style Profile (human vs machine
  columns); Rubric Scorecard (per-rule score, coverage, N/A visible);
  Coaching Insights (Strengths / Growth Areas / Notable Patterns); Topic
  Breakdown; Sessions This Period; plus a degraded-mode banner block when
  LLM sections are absent.
- Output contract: the template file only.
- Verify: `uv run python -c` renders it with a minimal context and the
  section headings appear.

**T28: report/generator.py**
- Role: Developer · Reasoning: no_think
- Description: build_report(ReportData) -> markdown string; assembles
  template context; when LLM results are None, emits the banner and skips
  LLM sections cleanly (the no-model path is the common case: the desktop
  GPU is often off).
- Input: T21, T23, T25 types, T27.
- Output contract: src/prompt_coach/report/generator.py only.
- Verify: `uv run python -c` renders both full and degraded variants.

**T29: tests/test_report.py**
- Role: Tester · Reasoning: no_think
- Verify: `uv run pytest tests/test_report.py -q`

**T30: query.py**
- Role: Developer · Reasoning: think
- Description: answer(question, cache, llm, k=12): FTS5 top-k retrieval,
  QUERY_ANSWER_V1 with citations (source, session id, date); llm=None path
  prints the matching snippets with refs instead.
- Input: T16, T18, T19.
- Output contract: src/prompt_coach/query.py only.
- Verify: `uv run python -c` with stub cache+LLM returns an answer containing
  a citation; with llm=None returns snippets.

**T31: Rewrite cli.py**
- Role: Developer · Reasoning: think
- Description: wire everything: `discover` (StoreInfo table), `stats`
  (metrics table, `--since`), `report` (`--since --limit --sample --no-llm
  --refresh --out`), `query`, `import` (via `@app.command("import")`: the
  current `import_` produces a mangled command name), `cache sync|clear|
  info`. No emojis (house style). `serve` prints "phase 2" and exits 1.
  Store failures degrade per the section 2 failure story.
- Input: all prior modules.
- Output contract: src/prompt_coach/cli.py only (rewrite).
- Verify: `uv run prompt-coach discover` lists hermes + claude-code with
  real counts; `uv run prompt-coach report --no-llm --since 30d` prints a
  report with the degraded banner when Ollama is off.

**T32: tests/test_cli.py**
- Role: Tester · Reasoning: no_think
- Description: typer CliRunner over every command with stores/cache
  redirected to tmp fixtures via env vars; asserts exit codes, degraded
  banner, import round-trip.
- Output contract: tests/test_cli.py only.
- Verify: `uv run pytest tests/test_cli.py -q`

**T33: Update README.md**
- Role: Developer · Reasoning: no_think
- Description: user-facing quick start matching the real command set and
  privacy story; no em dashes.
- Output contract: README.md only.
- Verify: `grep -q 'prompt-coach report' README.md && ! grep -q $'\u2014'
  README.md`

**T34: Tester gate**
- Role: Tester · Reasoning: think
- Description: the closeout check against this blueprint.
- Output contract: STATUS.md updated to complete (the only file written).
- Verify (all must pass):
  - `uv run pytest -q && uv run ruff check . && uv run black --check .`
  - `uv run prompt-coach discover` shows both live stores with counts
  - `uv run prompt-coach report --no-llm --since 30d` succeeds, < 60s
  - em-dash gates on all four docs; `! grep -q 'data/sessions[.]db'
    BLUEPRINT.md README.md` (AGENTS.md and DECISIONS.md may name the old
    wrong path when warning about it or recording the correction)
  - NFR spot-checks from section 4c (sync wall-clock, memory sanity)
  - when the desktop is on: `uv run prompt-coach report --since 7d
    --sample 20` renders rubric scorecard + patterns from the live model

---

### 7. MODEL STRATEGY

**Build execution**: Claude Code executes every task, interactively in this
session or via `claude -p` one-shots (user decision 2026-07-20; supersedes
the multi-tier routing in v1 and aligns with the pending 2-tier proposal in
hermes-skills DECISIONS.md). No hermes -z local-model routing for the build.

**Runtime analysis model** (a product config, not build routing):
- Default: qwen3-coder-30b:latest (32k num_ctx) via direct Ollama, http://192.168.1.123:11434/v1.
- Optional: llm-api gateway on localhost:8080/v1, only with local-pinned
  routing; the allow_remote guard enforces the conversation (section 4e).
- No model reachable: deterministic-only mode, clearly bannered. This path
  is first-class; the desktop GPU is frequently off.
- Prompting per prompting-standards: temp 0, JSON contracts in templates,
  one evidence-adding re-prompt then fail (B5), payload caps (section 4c).

---

### 8. DEPENDENCY GRAPH

```
T1 -> T2, T3, T4                     (docs; T2-T4 parallel after T1)
T5 -> T6 -> T7
T6 -> T8
T8 -> T9  -> T10                     (stores branch)
T8 -> T11 -> T12
T8 -> T13 -> T15;  T14 after T5 (feeds T15, T22)
T6, T8 -> T16 -> T17                 (cache branch)
T7 -> T18 -> T20;  T6 -> T19         (llm branch)
T6 -> T21 -> T22                     (metrics branch)
T6, T16, T18, T19 -> T23 -> T24
T6, T16, T18, T19 -> T25 -> T26      (parallel with T23)
T6 -> T27 -> T28 -> T29              (T28 also needs T21/T23/T25 types)
T16, T18, T19 -> T30
all modules -> T31 -> T32 -> T33 -> T34
```

Stores, cache, llm, and metrics branches are parallelisable after T8.

---

### 9. RISK REGISTER

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Gateway privacy leak: llm-api falls back to OpenRouter with prompt content | Medium if gateway used | Critical (breaks the product promise) | allow_remote guard in LocalLLM; direct-Ollama default; documented in 4e |
| Ollama effective num_ctx smaller than assumed (server unverified, was off during design) | Medium | Medium (truncated analyses) | conservative 8k payload cap; escalate-if on T25; check num_ctx before tuning |
| Hermes schema drift (we do not own state.db) | Medium over time | Medium | raw SQL touching few columns; T9 escalates on mismatch; store errors degrade, never crash |
| Claude Code JSONL format drift across versions | Medium over time | Medium | strict-but-skipping parser (unknown lines counted, not fatal); filters tested in T12 |
| Session-fork double counting skews metrics | High without mitigation | Medium | uuid/(hash,timestamp) dedupe in cache; tested in T17 |
| Model availability: desktop GPU off | High (observed during design) | Low by design | deterministic-only mode is first-class with banner |
| Cost escalation | N/A | - | local inference only; build uses existing Claude subscription |
| Data/state risk: cache corruption | Low | Low | cache is disposable; delete and resync |

---

### 10. ROLLBACK PLAN

| After completing... | To rollback... |
|----|-----|
| Any single task | `git revert` the task's commit; tasks are one-output so reverts are clean |
| Docs phase (T1-T4) | `git checkout` the v1 docs from history |
| Any point | `rm -rf ~/.cache/prompt-coach/` resets all derived state; source stores are never written, so there is nothing else to undo |
| Full build | reset to the pre-build tag; the repo returns to scaffold |

### 10b. DEPLOYMENT & RELEASE

N/A: local tool, run from the repo via `uv run prompt-coach` or installed
with `uv tool install .`. Nothing is deployed anywhere.

### 10c. OBSERVABILITY

N/A: interactive CLI; failures surface directly. One convention: `--verbose`
prints per-store sync counts and LLM cache hit rates (counts only, never
content).

### 10d. DOCUMENTATION

| Deliverable | Task | Owner |
|-------------|------|-------|
| BLUEPRINT.md (this) | T1 | Architect |
| AGENTS.md corrections | T2 | Architect |
| STATUS.md | T3 | Architect |
| DECISIONS.md | T4 | Architect |
| README.md quick start | T33 | Developer |

No API docs (no API in phase 1); no runbook (nothing operated).

---

### 11. ROLE HANDOFF PROMPTS

Claude Code executes everything, so handoffs are session-boot prompts rather
than paste-and-relay cards.

```
HANDOFF: DEVELOPER (also covers Tester tasks)
===========================================================
Read AGENTS.md, then BLUEPRINT.md sections 3, 4, 6, 8 of
~/projects/prompt-coach. Work the build plan in dependency order from
STATUS.md, one task at a time. For each task: implement exactly the output
contract (one file), run its Verify command, update STATUS.md, commit as
"T<n>: <name>". Never write to ~/.hermes/state.db or ~/.claude/projects.
If a fact is missing from the blueprint, inspect the live system or ask;
do not invent. Escalate per each task's Escalate-if line.
```

```
HANDOFF: TESTER GATE (T34)
===========================================================
Read BLUEPRINT.md sections 2, 4c, 6 (T34) and 12. Run every T34 check
verbatim. Any failure: record in STATUS.md blockers, do not mark complete.
```

---

### 12. POST-BUILD CHECKLIST

- [ ] All T1-T33 Verify commands pass; T34 gate green
- [ ] Coverage matrix: every row satisfied or N/A'd
- [ ] No hardcoded secrets; config holds no cloud keys
- [ ] File structure matches section 4.1
- [ ] AGENTS.md canonical, facts correct, no em dashes in any doc
- [ ] User stories: happy, edge (no LLM), failure (missing store) all verified
- [ ] NFR targets met on the real corpus (section 4c)
- [ ] Privacy: egress guard tested (T20); no content in logs; erasure = cache delete
- [ ] Store read-only: no code path opens a source store writable
- [ ] STATUS.md reflects completion; DECISIONS.md current

---

### 13. PROGRESS TRACKING

`STATUS.md` at the repo root (created in T3). Every task updates it. Plain
English only, no section-number references.

### 14. DECISIONS & CHANGE LOG

`DECISIONS.md` at the repo root (created in T4, seeded with the six
decisions of record). Append on any scope change, spec patch, or assumption
that turns out wrong.

---

### 15. FUTURE DIRECTIONS (phase 2+, explicitly out of scope now)

- Trend analysis: metric snapshots over time, "vs baseline" deltas
- `serve`: read-only HTTP API (FastAPI returns to the dependency list then)
- OpenWebUI store (remote, needs auth) and llm-api usage-log enrichment
- OpenAI/Claude.ai export readers
- Hermes skill: weekly coaching report via cron to Slack
- Prompt library extraction: best prompts as reusable templates

---

### 16. PHASE 2 (added 2026-07-20, after phase-1 gate)

Scope decided with Alistair: Copilot store, ChatGPT export import, dash
visualisation. llm-api integration skipped entirely (its UsageLog stores no
message content by design; see DECISIONS.md). Everything below inherits the
phase-1 invariants: read-only sources, unified Prompt model and cache,
origin segmentation, privacy guard, no em dashes, per-task commits.

#### 16.1 Store: Copilot Chat (verified against live files 2026-07-20)

Path (WSL reading the Windows profile directly):
`/mnt/c/Users/<user>/AppData/Roaming/Code/User/workspaceStorage/<ws>/chatSessions/<session-uuid>.jsonl`
155 session files across 154 workspaces today. Config override:
`PROMPT_COACH_COPILOT_DIR`. Native-Linux VS Code path
(`~/.config/Code/User/workspaceStorage`) is probed as a fallback candidate.

Each line is a JSON-patch event: `{"kind": K, "k": path, "v": value}`.
Prompts are extracted from exactly two shapes:
- kind 0: initial state; `v.requests[]` may already hold requests
- kind 2 with `k == ["requests"]`: appends request objects
A request carries `message.text` (the typed prompt), `requestId` (the
message ref), and `timestamp` (epoch ms). kind 1 events mutate existing
paths and never introduce prompts, so per-file offset resume stays valid;
the session id is the filename stem so resumed parses never need the
kind-0 line again.

#### 16.2 Store: ChatGPT export (UNVERIFIED format)

No local ChatGPT store exists; input is the official data-export ZIP or its
`conversations.json`. Documented shape: a list of conversations, each with
`title`, `create_time`, and a `mapping` tree of nodes whose
`message.author.role == "user"` and `message.content.content_type ==
"text"` carry `content.parts[]`. Hidden/system nodes are skipped. The
`import` command auto-detects this shape (list items with a `mapping` key)
vs the simple v1 JSON format. Status: built to the documented format,
UNVERIFIED until run against a real export.

#### 16.3 dash

`prompt-coach dash [--since] [--plain]` renders with rich (already
installed via typer): summary header; per-store weekly volume sparklines;
human/machine split; style metrics table; deterministic rubric scorecard
with colour thresholds. No LLM calls; no prompt content ever rendered
(counts, rates, scores only); non-TTY output auto-degrades and --plain
forces it.

#### 16.4a Store: Codex CLI (added 2026-07-21, verified against live files)

Path: `~/.codex/sessions/YYYY/MM/DD/rollout-<timestamp>-<uuid>.jsonl`. On
WSL the Windows profile is read via `/mnt/c/Users/<user>/.codex/sessions`;
a native Linux path is probed as a fallback. Config override
`PROMPT_COACH_CODEX_DIR`. One session file per conversation (filename stem
is the session uuid), append-only, so byte-offset resume applies (same
`iter_files`/`iter_file` contract as Copilot and Claude Code).

Each line is `{"timestamp", "type", "payload"}`. `type=session_meta` (the
first line) carries `payload.id` and `payload.cwd`. Prompts come from
`type=event_msg` lines where `payload.type == "user_message"` and
`payload.kind == "plain"`; `kind == "environment_context"` is sandbox/IDE
boilerplate injected every turn and is dropped, never a real prompt. The
VS Code extension (`originator: codex_vscode`) additionally wraps real
requests in an IDE-context block (active file, open tabs, files
mentioned); when the `## My request for Codex:` marker is present, only
the text after it is kept. No per-message id exists in the format, so
`message_ref` falls back to a content hash (same pattern as Copilot).
`cwd`/`git_repo` come from the session's `session_meta` line; on a resumed
(incremental) read that starts past `session_meta`, `cwd` is `None`.

Verified live 2026-07-21 against the one real session file on this
machine (VS Code extension, Oct 2025, wedding-site project): extraction
produced clean human-request text with the IDE wrapper correctly
stripped.

#### 16.4 Phase-2 build tasks

All tasks: Role Developer/Tester, Model Claude Code, temp 0, same
escalation and self-check rules as section 6.

**T35: stores/copilot.py + tests**
- Output contract: src/prompt_coach/stores/copilot.py, then
  tests/test_stores_copilot.py (two commits).
- Description: reader per 16.1 with iter_files/iter_file (byte offsets) so
  the existing cache file-sync path handles incremental resync; malformed
  lines skipped and counted; discover probes /mnt/c and native-Linux
  candidates and reports unavailable cleanly elsewhere.
- Verify: `uv run pytest tests/test_stores_copilot.py -q` and a live
  `discover` showing the copilot store with a session count.
- Escalate if: a live session file shows prompts arriving via any event
  shape other than kind 0 / kind 2 ["requests"].

**T36: stores/chatgpt_export.py + tests**
- Output contract: src/prompt_coach/stores/chatgpt_export.py, then
  tests/test_stores_chatgpt.py (two commits).
- Description: reader per 16.2 accepting .zip or .json; import command
  auto-detects chatgpt vs simple format; unverified status recorded.
- Verify: `uv run pytest tests/test_stores_chatgpt.py -q` against an
  inline synthetic export fixture.

**T37: dash rendering module + tests**
- Output contract: src/prompt_coach/report/dash.py, then
  tests/test_dash.py (two commits).
- Description: pure build_dash(data) -> rich renderable per 16.3;
  sparkline helper with block characters; colour thresholds green >= 0.7,
  yellow >= 0.4, red below.
- Verify: `uv run pytest tests/test_dash.py -q`.

**T38: CLI wiring**
- Output contract: cli.py + config.py updated (one commit).
- Description: dash command; copilot store in default_stores/discover;
  import auto-detection; PROMPT_COACH_COPILOT_DIR config.
- Verify: `uv run prompt-coach dash --plain` renders on the live cache;
  `uv run prompt-coach discover` lists copilot.

**T39: docs (this section, DECISIONS, STATUS, README, AGENTS)**
- Verify: em-dash gates; README mentions dash and the new stores.

**T40: phase-2 gate**
- Verify: full suite + ruff + black; live discover shows copilot counts;
  full resync stays under the section 4c NFRs; dash renders on real data
  in both rich and --plain modes; STATUS.md updated.

Dependency order: T35 and T36 parallel after docs; T37 parallel with both;
T38 after T35+T37; T39 anytime; T40 last.

**T41: stores/codex_cli.py + tests (added 2026-07-21, post-gate)**
- Trigger: live-LLM smoke pick-up session surfaced a real `~/.codex/`
  directory on this machine (Codex CLI via the VS Code extension); the
  user redirected priority toward CLI agent harnesses over the
  unverified ChatGPT web-export path (see DECISIONS.md).
- Output contract: src/prompt_coach/stores/codex_cli.py, then
  tests/test_stores_codex.py (one commit; small enough not to split).
- Description: reader per 16.4a; SourceKind.CODEX added to models.py;
  PROMPT_COACH_CODEX_DIR config; wired into default_stores/discover.
- Verify: `uv run pytest tests/test_stores_codex.py -q`; live `discover`
  and `cache sync` against the real session file, confirming clean
  extracted content (no IDE-wrapper noise).

---

### 17. PHASE 3 (added 2026-07-29/30): model fit, nudge latency, setup wizard

Three post-gate additions in one session, each with a full DECISIONS.md
entry (trigger/decision/why/affects); this section records the resulting
shape, not the reasoning (see DECISIONS.md for that).

**17.1 Model fit** (`analysis/model_fit.py`, 2026-07-29): a new deterministic
analysis dimension flagging prompts where what they demanded and the model
that handled them look mismatched. `Prompt.model` is now captured by all
four live stores (per-turn for Claude Code/Codex CLI, per-request for
Copilot, session-level for Hermes -- no per-message model column exists
there). `ModelFitConfig.mode` (off/descriptive/prescriptive, default
descriptive) mirrors `NudgeConfig`. Surfaced in both `dash` and `report`.
Known gap: the cache dedupes on insert, so prompts cached before this
shipped never retroactively gain a `model` value -- a one-time `cache
clear` backfills it.

**17.2 Nudge latency fix** (cli.py/nudge.py import structure, 2026-07-29):
`cli.py` imported the entire CLI (report/rubric/pattern analysis, all four
stores) at module scope, and `nudge.py` imported the `openai` SDK
unconditionally -- together costing every single prompt submission
~1.3-1.5s regardless of nudge mode, dominated by `openai`'s own unused
type surface (~700-900ms). Fixed via imports local to the commands/
functions that actually need them (`TYPE_CHECKING`-guarded where only a
type hint was needed). Measured ~3x improvement for the common case
(mode=off/non-triggering). No behavior change, no new files.

**17.3 Setup wizard + per-directory nudge + store enable/disable**
(2026-07-29/30): `install.sh` (repo root) wraps `uv tool install .`
mirroring Hermes's own setup shape, minus the manual venv/symlink work
uv's tool-install mechanism already does. New `prompt-coach setup`
command: per-store enable/disable (`StoresConfig.enabled`, opt-in, default
all four -- section 4.6's `default_stores()` now filters on it), LLM
endpoint/model with a live reachability check, nudge/model-fit modes, and
an optional per-directory nudge override, then offers to run `report` or
`dash` immediately. `NudgeConfig.dir_overrides` (config.toml-only, a path
-> mode mapping) exists because Claude Code's own hook scopes merge
rather than override -- there is no settings-file way to exclude one
project from a globally-registered hook, confirmed against the live hooks
docs, so `nudge.py` resolves the longest matching `cwd` prefix itself.
`config.py` gained a hand-formatted TOML writer (`write_config`,
round-trip-safe with `load_config`) rather than a new TOML-writing
dependency. 231 tests total (up from 213): tests/test_config.py (new),
test_nudge.py/test_cli.py extended.
