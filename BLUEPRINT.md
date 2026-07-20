================================================================
PROJECT BLUEPRINT — prompt-coach
================================================================
Generated: 2026-07-20
Architect:  Hermes (deepseek-v4-flash) via blueprint-orchestration
Project:    prompt-coach — Local-first personal prompt coach
Platforms:  CLI tool (Python) + optional read-only HTTP API
Status:     HANDSOVER — ready for implementation

> Read the full market-gap validation in §0. The short version:
> existing tools optimise prompts (DSPy, promptfoo) or measure spend
> (Langfuse, Datadog) or study populations (datasets) — none analyse
> *your individual prompting behaviour* from your actual history and
> give you personalised coaching. The gap exists because SaaS can't
> solve it (privacy) and the coaching angle is hard to get right.
> This tool solves it locally, with home-grown AI, zero data export.

================================================================

### 0. COVERAGE MATRIX

| Concern | Covered by |
|---------|-----------|
| Market gap validation | §1 · Validated with user in session 2026-07-20 |
| Product vision | §2 · Privacy-first personal prompt analytics |
| Architecture | §3 · Plugin store readers + local LLM analysis |
| Session store support | §4 · Hermes SQLite, OpenWebUI, generic JSON |
| Analysis pipeline | §5 · Pattern extraction, topic clustering, style metrics |
| Reporting | §6 · Markdown reports, CLI output, optional JSON |
| CLI design | §7 · discover, report, query, serve commands |
| Integration with llm-api | §8 · Usage log ingestion, model routing |
| Implementation plan | §9 · Phased tasks for Claude to execute |
| Testing | §10 · Unit, integration, snapshot tests |

---

### 1. VALIDATED MARKET GAP

**The insight (from the original slide):** Existing tooling falls into
three categories, and the intersection of all three is empty:

1. **Optimizers** (DSPy, promptfoo, Anthropic Improver) — improve a single
   prompt, never the prompter. Nothing transferable.
2. **Observability** (Langfuse, Helicone, Datadog) — measure tokens, cost,
   latency, output. Never critique the *human's* style.
3. **Corpora** (datasets, population research) — aggregate patterns across
   many people. Never individual.

**The gap:** A tool that reads YOUR actual prompt history, analyses YOUR
personal prompting style, and gives YOU a coaching report.

**Why it exists (validated 2026-07-20):**
- Privacy — nobody wants to upload their prompt history to a SaaS. This is
  the primary moat. The only way to solve it is local-first.
- Genuinely useful coaching is hard — distinguishing signal from noise in
  prompt patterns requires a local LLM with context.
- The market is small for a paid product, but the *personal utility* for
  power users who already have the data is high.

**Why build it now:**
- You already have a rich session store (Hermes SQLite) with hundreds of
  conversations
- You already run local models (Ollama on 192.168.1.123:11434)
- You already have llm-api logging every request with token usage
- The privacy moat means no SaaS competitor can easily replicate this

---

### 2. PRODUCT VISION

**Name:** prompt-coach (or "proco" for short)

**Tagline:** *Your personal prompting analyst. Runs locally. Zero data export.*

**What it does:**
1. **Discovers** session stores on your machine (Hermes, OpenWebUI, etc.)
2. **Extracts** your prompt history — what you actually typed, not templates
3. **Analyses** patterns via a local LLM — topic clustering, style metrics,
   recurring patterns, effectiveness signals
4. **Reports** insights as a markdown briefing — "here's how you prompted
   this week, what's working, what's not"

**What it is NOT:**
- NOT a prompt optimizer (it doesn't rewrite prompts)
- NOT an observability dashboard (it doesn't track tokens/cost — that's
  llm-api's job)
- NOT a SaaS product (it's local-only, CLI-first)

**User personas:**
- **Primary:** Alistair — power user, runs Hermes + llm-api + Ollama,
  wants to understand his own prompting patterns and improve
- **Secondary:** Anyone with a local LLM setup and a session store who
  wants personal analytics on their LLM usage

---

### 3. ARCHITECTURE

```
┌─────────────────────────────────────────────────────────┐
│                    prompt-coach                          │
│                                                         │
│  ┌──────────────┐  ┌──────────────┐  ┌───────────────┐ │
│  │  discover     │  │  extract      │  │  analyse      │ │
│  │  (find stores)│─▶│  (read history)│─▶│  (LLM pipeline)│ │
│  └──────────────┘  └──────────────┘  └───────┬───────┘ │
│                                              │         │
│  ┌──────────────┐  ┌──────────────┐          │         │
│  │  report      │◀─│  format      │◀─────────┘         │
│  │  (CLI output)│  │  (markdown)  │                    │
│  └──────────────┘  └──────────────┘                    │
│                                                         │
│  ┌──────────────┐                                       │
│  │  serve       │  (optional read-only HTTP API)       │
│  └──────────────┘                                       │
└─────────────────────────────────────────────────────────┘
         │
         ▼
┌──────────────────┐    ┌──────────────────┐
│  Session stores   │    │  Local LLM       │
│  (Hermes SQLite,  │    │  (Ollama via     │
│   OpenWebUI,      │    │   llm-api or     │
│   JSON exports)   │    │   direct)        │
└──────────────────┘    └──────────────────┘
```

**Data flow:**
1. `discover` scans well-known paths for session stores
2. `extract` reads raw conversations (prompts only, not responses)
3. `analyse` sends batches of prompts to a local LLM with structured
   prompts for analysis (topic clustering, style metrics, pattern detection)
4. `format` renders the analysis as a readable markdown report
5. `report` CLI command ties it all together

**Key design decisions:**
- **Prompts only, not responses** — the coaching signal is in what the
  user types, not what the model returns. Responses are excluded to save
  tokens and privacy.
- **Local LLM only** — never calls an external API. Falls back to a
  simple rule-based analysis if no local model is available.
- **Batched processing** — large histories are processed in chunks to
  stay within context windows.

---

### 4. SESSION STORE SUPPORT

#### 4.1 Hermes SQLite (primary target)

Hermes stores sessions in `~/.hermes/data/sessions.db` (SQLite).

Key tables (from inspection):
- `sessions` — id, title, created_at, updated_at, profile_id
- `messages` — id, session_id, role (user/assistant/tool), content, created_at
- `profiles` — id, name

Extractor reads messages where `role = 'user'` and groups by session.

#### 4.2 OpenWebUI (secondary target)

OpenWebUI stores sessions in its Postgres or SQLite database.

Channel model: `chats` table with `id`, `title`, `user_id`, `messages` (JSONB).

Extractor reads the `messages` JSONB field and extracts user messages.

#### 4.3 Generic JSON import (tertiary target)

A `--import` flag that accepts a JSON file with format:
```json
[
  {
    "session_id": "...",
    "title": "...",
    "timestamp": "ISO-8601",
    "messages": [
      {"role": "user", "content": "..."},
      {"role": "assistant", "content": "..."}
    ]
  }
]
```

#### 4.4 Store abstraction

Each store implements a `SessionStore` protocol:
```python
class SessionStore(Protocol):
    name: str
    def discover(self) -> list[StorePath]: ...
    def read_sessions(self, path: StorePath, limit: int = 50, since: str | None = None) -> list[Session]: ...
```

---

### 5. ANALYSIS PIPELINE

The analysis runs in stages, each stage calling the local LLM with a
structured prompt.

#### Stage 1: Topic clustering

For each session, extract the topic/domain from the user's messages.

**LLM prompt:**
```
Given these user messages from an LLM session, identify the single
topic/domain (e.g. "coding/python", "writing/email", "research/health",
"creative/writing"). Output only the topic label.
```

#### Stage 2: Style metrics

For each session, compute structural metrics:
- Average prompt length (tokens)
- Number of follow-up messages per session
- Whether the prompt includes examples, constraints, or structured output
- Whether the user iterated (refined the prompt after the first response)
- Front-loading ratio (how much of the request is in the first message)

**Rule-based** (no LLM call):
```python
@dataclass
class StyleMetrics:
    avg_prompt_tokens: float
    median_prompt_tokens: float
    prompts_per_session: float
    refinement_rate: float          # % of sessions with >1 user message
    example_rate: float             # % of prompts containing examples
    constraint_rate: float          # % of prompts with explicit constraints
    structured_output_rate: float   # % of prompts asking for JSON/table/etc.
    avg_first_message_ratio: float  # % of total content in first message
```

#### Stage 3: Pattern detection

Identify recurring patterns, strengths, and weaknesses across all sessions.

**LLM prompt (batched across sessions):**
```
You are a prompt coaching analyst. Given these {N} sessions from a user's
prompt history, identify:

1. TOP 3 STRENGTHS — what does this user consistently do well?
   (e.g. "gives clear examples", "specifies output format", "provides context")

2. TOP 3 GROWTH AREAS — what patterns consistently lead to suboptimal results?
   (e.g. "vague requests without examples", "overly long prompts that lose focus")

3. NOTABLE PATTERNS — recurring approaches, habits, or blind spots

4. DOMAIN DISTRIBUTION — what topics does the user spend most prompting time on?

Be specific and reference actual patterns from the data. Avoid generic advice.
```

#### Stage 4: Trend analysis

When run with `--since 30d` or against multiple snapshots, compare metrics
over time to detect improvement or regression.

#### Stage 5: Report generation

The report is a markdown document with sections:

```markdown
# Prompt Coach Report — 2026-07-20
## Summary
- {N} sessions analysed, {M} total prompts
- {X} topics, top: {topic1}, {topic2}
- Avg prompt length: {tokens} tokens

## Style Profile
| Metric | Value | vs Baseline |
|--------|-------|-------------|
| Avg prompt length | 180 tokens | +12% |
| Refinement rate | 65% | +5% |
| Example rate | 40% | new |
| ...

## Coaching Insights
### Strengths
- ...

### Growth Areas
- ...

### Notable Patterns
- ...

## Topic Breakdown
| Topic | Sessions | % of total |
|-------|----------|------------|
| Coding | 24 | 48% |
| Writing | 12 | 24% |
| ...

## Sessions this period
{list of session titles + dates + topic labels}
```

---

### 6. CLI DESIGN

```bash
# Discover session stores
prompt-coach discover
# Output:
#   Found 3 stores:
#   [1] Hermes SQLite  ~/.hermes/data/sessions.db  (247 sessions)
#   [2] OpenWebUI      http://192.168.1.123:3000    (89 sessions, needs auth)
#   [3] JSON import    ~/exports/                    (2 files)

# Generate a report
prompt-coach report [--store 1] [--since 7d] [--limit 50]
# Output: full markdown report to stdout

# Quick overview
prompt-coach stats [--store 1]
# Output: compact summary table

# Query — ask a question about your prompt history
prompt-coach query "What topics did I work on last week?"
# Output: LLM-generated answer based on session data

# Serve — read-only HTTP API for integration
prompt-coach serve --port 9090
# Endpoints:
#   GET /v1/sessions  — list sessions
#   GET /v1/report    — generate report
#   GET /v1/health    — health check

# Import — import external session data
prompt-coach import --file ~/exports/sessions.json
```

---

### 7. INTEGRATION WITH LLM-API

prompt-coach and llm-api are separate repos but designed to work together:

**Option A — llm-api as model router:**
prompt-coach calls `http://localhost:8080/v1/chat/completions` with a
gateway key, routing through llm-api's catalog. This means the model
choice respects llm-api's configuration (which Ollama models, OpenRouter
fallback, etc.).

**Option B — direct Ollama:**
prompt-coach calls `http://192.168.1.123:11434/v1/chat/completions`
directly, bypassing the gateway. Simpler, no auth dependency.

**Config precedence:** `PROMPT_COACH_API_BASE` env var, then
`~/.config/prompt-coach/config.toml`, then default to direct Ollama.

**Usage log ingestion:**
prompt-coach can read llm-api's usage log table (if Postgres is shared)
or a JSON export for analytics. This enriches the report with actual
token spend per session/topic.

---

### 8. PROJECT STRUCTURE

```
~/projects/prompt-coach/
├── AGENTS.md              # Agent guidance (this file is canonical)
├── BLUEPRINT.md           # Full spec (this document)
├── pyproject.toml         # Project config
├── .gitignore
├── README.md              # Short user-facing readme
├── src/
│   └── prompt_coach/
│       ├── __init__.py
│       ├── __main__.py         # CLI entry: `python -m prompt_coach`
│       ├── cli.py              # Typer/Click CLI app
│       ├── config.py           # Config loading (env, toml, defaults)
│       ├── stores/
│       │   ├── __init__.py
│       │   ├── base.py         # SessionStore protocol + Session dataclass
│       │   ├── hermes.py       # Hermes SQLite reader
│       │   ├── openwebui.py    # OpenWebUI reader (HTTP API)
│       │   └── json_import.py  # Generic JSON import
│       ├── analysis/
│       │   ├── __init__.py
│       │   ├── metrics.py      # Rule-based style metrics calculator
│       │   ├── topics.py       # LLM-based topic clustering
│       │   ├── patterns.py     # LLM-based pattern detection
│       │   └── trends.py       # Trend analysis over time
│       ├── report/
│       │   ├── __init__.py
│       │   ├── generator.py    # Report generation (markdown)
│       │   └── templates/      # Jinja2 report templates
│       ├── llm/
│       │   ├── __init__.py
│       │   ├── client.py       # Local LLM client (OpenAI-compatible)
│       │   └── prompts.py      # Analysis prompts
│       └── server/
│           ├── __init__.py
│           └── app.py          # Optional read-only HTTP API (FastAPI)
├── tests/
│   ├── __init__.py
│   ├── conftest.py
│   ├── fixtures/
│   │   └── sample_sessions.json  # Test data
│   ├── test_metrics.py
│   ├── test_stores_hermes.py
│   ├── test_stores_json.py
│   ├── test_report_generator.py
│   └── test_cli.py
└── docs/
    └── architecture.md       # (optional, BLUEPRINT.md is primary)
```

---

### 9. IMPLEMENTATION PLAN

#### Phase 1 — Core (Tasks 1-6)

**Task 1: Project skeleton + config**
- Create pyproject.toml with deps (typer, openai, httpx, sqlalchemy, aiosqlite, jinja2, pydantic)
- Create CLI entry point with `--help`
- Create config loader (env vars + TOML)
- Verify: `uv sync && uv run prompt-coach --help` works

**Task 2: Session store abstraction + Hermes reader**
- Implement `Session` dataclass (id, title, timestamp, messages, store)
- Implement `SessionStore` protocol
- Implement Hermes SQLite reader (reads from ~/.hermes/data/sessions.db)
- Verify: `prompt-coach discover` shows Hermes store with session count

**Task 3: Style metrics calculator**
- Implement rule-based metrics (prompt length, refinement rate, example rate, etc.)
- Write tests with sample session data
- Verify: metrics output is deterministic and correct

**Task 4: Local LLM client**
- Implement OpenAI-compatible client for local Ollama
- Implement prompt templates for topic clustering and pattern detection
- Graceful fallback if no local model is available (rule-based only)
- Verify: can call local model and get structured output

**Task 5: Topic clustering + pattern detection**
- Implement LLM-based topic clustering per session
- Implement LLM-based pattern detection across sessions
- Implement report generator (markdown output)
- Verify: `prompt-coach report` produces a readable report

**Task 6: Stats command + JSON import**
- Implement `prompt-coach stats` (compact table output)
- Implement JSON import format
- Verify: `prompt-coach stats --import ~/exports/sessions.json` works

#### Phase 2 — Advanced (Tasks 7-9)

**Task 7: Trend analysis**
- Store snapshots of metrics over time
- Compare current vs baseline in report
- Implement `--since` filtering

**Task 8: Query command**
- Implement `prompt-coach query "..."` — natural language query against session history
- Uses local LLM to answer questions about past conversations
- Verify: "what did I work on last week?" returns relevant sessions

**Task 9: Serve command (read-only HTTP API)**
- FastAPI app with GET /v1/sessions, /v1/report, /v1/health
- Verify: `curl localhost:9090/v1/health` returns OK

---

### 10. TESTING STRATEGY

| Layer | Tool | What to test |
|-------|------|-------------|
| Metrics | pytest | Deterministic, no LLM. Test with sample sessions. |
| Stores | pytest + aiosqlite | Hermes reader with fixture DB, JSON reader with fixture files |
| Report | pytest | Markdown output contains expected sections |
| CLI | pytest | Typer test app, verify command parsing and output |
| LLM | pytest (mock) | Mock httpx/OpenAI to verify prompt templates are correct |
| E2E | manual | Point at real Hermes DB, verify output makes sense |

**Test fixtures:**
- `tests/fixtures/sample_sessions.json` — 5-10 sessions with varied prompting styles
- `tests/fixtures/hermes_test.db` — minimal Hermes SQLite clone with 3-5 sessions

---

### 11. DEPENDENCIES

```toml
dependencies = [
    "typer>=0.15,<0.16",
    "openai>=1.68,<2",
    "httpx>=0.28,<0.29",
    "sqlalchemy>=2.0.36,<2.1",
    "aiosqlite>=0.20,<0.21",
    "jinja2>=3.1,<4",
    "pydantic>=2.10,<3",
    "tomli>=2.2,<3",  # Python <3.11 compat
]

[project.optional-dependencies]
dev = [
    "pytest>=8.3,<9",
    "pytest-asyncio>=0.25,<0.26",
    "ruff>=0.9,<0.10",
    "black>=24.10,<25",
    "respx>=0.22,<0.23",  # mock httpx for LLM client tests
]
```

---

### 12. CONFIGURATION

```toml
# ~/.config/prompt-coach/config.toml

[llm]
# Default: direct Ollama
api_base = "http://192.168.1.123:11434/v1"
# Or via llm-api gateway:
# api_base = "http://localhost:8080/v1"
# api_key = "sk-..."  # only needed for llm-api gateway

model = "qwen3-coder:30b"  # default analysis model

[stores]
# Hermes DB path (auto-detected if not set)
hermes_db = "~/.hermes/data/sessions.db"

[report]
# Default report format
format = "markdown"  # or "json"
include_sessions = true  # include session list in report
max_sessions = 50  # max sessions to analyse per report
```

---

### 13. EDGE CASES & PITFALLS

**Empty session store:** `discover` finds no stores → "No session stores found.
Use --import to load external data."

**No local LLM available:** All LLM-based analysis degrades gracefully to
rule-based metrics only. Report includes: "LLM unavailable — running
rule-based analysis only."

**Very large history:** Processing 1000+ sessions could be slow. Default
limit to 50, add `--limit` and `--since` flags. Show progress bar.

**Encrypted/obfuscated stores:** Not supported. Only plaintext session stores.

**Multi-profile:** Hermes supports profiles. Discover should list all
profiles and let the user select with `--profile`.

**Non-English prompts:** Analysis should work with any language the local
LLM supports. Report language matches the prompt language.

**Privacy-critical:** Never log prompt content to disk. Never send data
to external APIs. The config file should not store API keys for external
services (only for local Ollama/gateway auth).

---

### 14. GIT SETUP

```bash
cd ~/projects/prompt-coach
git init
git add .
git commit -m "Initial scaffold: project skeleton, AGENTS.md, BLUEPRINT.md"
# Remote TBD — likely GitHub under aawobdev/prompt-coach
```

---

### 15. FUTURE DIRECTIONS (post-Phase 2)

- **Hermes skill integration** — a `prompt-coach-report` skill that runs
  weekly via cron, delivering a personal coaching report to Slack
- **OpenAI export format** — read ChatGPT conversation exports
- **Claude export format** — read Claude.ai conversation exports
- **Prompt library extraction** — automatically extract best prompts as
  reusable templates, push to a shared skill repo
- **Knowledge graph** — extract facts/decisions from conversations into a
  searchable personal knowledge base