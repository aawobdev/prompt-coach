"""Regenerate the README screenshots in docs/screenshots/.

Builds a synthetic home (three demo projects, Claude Code and Codex history,
Hermes cron jobs for the machine column), runs the real CLI against it with
colour forced on, and converts the ANSI output to SVG with rich's exporter.
No real prompt history is read: every store path and the cache point into a
temporary directory.

    uv run python scripts/make_screenshots.py            # deterministic shots
    uv run python scripts/make_screenshots.py --nudge    # also the nudge shot

--nudge calls the LLM endpoint in your own config.toml to rewrite one
synthetic prompt, so the screenshot shows a real rewrite.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import random
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from rich.console import Console, Group
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "screenshots"
END = datetime(2026, 10, 9, 17, 0, tzinfo=UTC)
WIDTH = 104

PROJECTS = {
    "acme-api": "# acme-api\n\nFastAPI service for orders, customers and invoices. Python "
    "3.12, managed with uv. Postgres 16 in production, SQLite in tests.\n\n"
    "## Commands\n\n- `uv run pytest` before every commit; CI runs the same.\n"
    "- `uv run ruff check . && uv run black --check .` for lint and format.\n"
    "- `alembic revision --autogenerate -m <msg>` for schema changes. Never edit an "
    "applied migration by hand.\n\n"
    "## Layout\n\n- `app/routes/`: one module per resource, thin handlers only.\n"
    "- `app/services/`: business logic; this is where tests should focus.\n"
    "- `app/models/`: SQLAlchemy models. Every table has created_at and updated_at.\n\n"
    "## Conventions\n\n- Type hints everywhere. No bare `except`.\n"
    "- Errors return RFC 7807 problem+json with a stable `type` URL.\n"
    "- Money is integer pence, never floats. Currency lives next to the amount.\n"
    "- New endpoints need a test for the happy path, a validation failure and an auth "
    "failure.\n\n## Don't\n\n- Don't add dependencies without asking.\n"
    "- Don't touch `app/legacy/`; it is being deleted in Q4.\n",
    "web-dashboard": "# web-dashboard\n\nReact + Vite.\n",
    "infra": "See README.md.\n",
}
DOC_NAMES = {"acme-api": "AGENTS.md", "web-dashboard": "CLAUDE.md", "infra": "CLAUDE.md"}

WEAK = [
    "make the dashboard better",
    "fix the tests",
    "why is this slow",
    "clean this up",
    "add auth",
    "it's broken again, can you look",
    "do the same for the other endpoints",
    "update the readme",
    "can you refactor this",
    "the build fails",
]
MEDIUM = [
    "The /orders endpoint returns 500 when the cart is empty. Find the cause in "
    "app/routes/orders.py and fix it.",
    "Add pagination to GET /customers using limit and offset query params, default 50.",
    "Write a migration that adds a nullable `archived_at` timestamp to the projects table.",
    "Make the sidebar collapse on screens narrower than 768px.",
    "Why does `terraform plan` want to recreate the NAT gateway every run?",
    "Rename `get_usr` to `get_user` everywhere and update the call sites.",
    "Add a loading skeleton to the invoices table while the query is in flight.",
]
STRONG = [
    "In app/routes/orders.py, add a `status` filter to GET /orders. Accept one or more "
    "of pending|paid|refunded as a comma-separated query param; reject anything else "
    "with 422 problem+json. Add tests in tests/test_orders.py covering a single status, "
    "several, and an invalid value. Don't touch the response schema.",
    "Write a `slugify(title: str) -> str` helper in app/utils/text.py. Lowercase, "
    "spaces and underscores become hyphens, strip other punctuation, collapse repeats, "
    "max 80 chars. Example: slugify('Hello_World! 2x') == 'hello-world-2x'. Stdlib "
    "only. Output just the function and its docstring.",
    "Review the diff on this branch for correctness bugs only, not style. Return a table: "
    "file:line | issue | suggested fix. If you find nothing, say so in one line.",
    "Port the revenue chart from Chart.js to Recharts. Keep the same colours, tooltip "
    "format (GBP, no decimals) and legend order. Done when `npm test` passes and the "
    "storybook story renders with the fixture data in src/fixtures/revenue.json.",
    "The nightly backup job in infra/cron/backup.sh exits 0 even when rsync fails. Make "
    "it fail loudly: set -euo pipefail, check rsync's exit code, and post to the "
    "#ops webhook on failure. Keep it POSIX sh, no bash-isms.",
]
STRUCTURED = [
    "Add rate limiting to the public API.\n\nRequirements:\n- 100 requests/minute per "
    "API key, 20 for anonymous\n- Return 429 with a Retry-After header\n- Store counters "
    "in Redis; fall back to in-memory when REDIS_URL is unset\n\nOutput: the middleware in "
    "app/middleware/ratelimit.py plus tests. Don't change existing routes.",
    "## Goal\nShow overdue invoices on the dashboard home page.\n\n## Details\n- New card "
    "above the revenue chart, red badge with the count\n- Click opens /invoices?status="
    "overdue\n- Hide the card when the count is 0\n\n## Done when\n- `npm test` passes\n"
    "- Storybook has a story for 0, 3 and 120 overdue",
]
MACHINE = [
    "TASK: summarise yesterday's failed CI runs across acme-api and web-dashboard.\n"
    "Verify: every run id in the summary exists in the CI API response.\n"
    "Output: markdown table, one row per run.",
    "TASK: check the TLS certificates on all public hosts expire in more than 14 days.\n"
    "Verify: openssl s_client output for each host.\nOutput: JSON list of host, days_left.",
    "TASK: draft release notes for acme-api from merged PRs since the last tag.\n"
    "Output: markdown, grouped by feat/fix/chore.",
]


def _ts(rng: random.Random, week: int) -> datetime:
    day = END - timedelta(weeks=11 - week) + timedelta(days=rng.randint(-6, 0))
    return day.replace(hour=rng.randint(8, 21), minute=rng.randint(0, 59))


def _pick(rng: random.Random, week: int) -> str:
    # Quality improves over the 12 weeks, as if the coaching is working.
    strong = 0.12 + 0.03 * week
    weak = max(0.45 - 0.03 * week, 0.12)
    r = rng.random()
    if r < strong:
        return rng.choice(STRONG + STRUCTURED)
    if r < strong + weak:
        return rng.choice(WEAK)
    return rng.choice(MEDIUM)


def build_home(home: Path) -> None:
    rng = random.Random(7)  # noqa: S311 -- seeded demo data, not security
    for name, doc in PROJECTS.items():
        (home / "projects" / name).mkdir(parents=True)
        (home / "projects" / name / DOC_NAMES[name]).write_text(doc)

    claude = home / ".claude" / "projects"
    codex = home / ".codex" / "sessions"
    for week in range(12):
        sessions = 3 + week + rng.randint(-1, 2)
        for _ in range(sessions):
            project = rng.choice(list(PROJECTS))
            cwd = str(home / "projects" / project)
            sid = str(uuid.UUID(int=rng.getrandbits(128)))
            start = _ts(rng, week)
            n_turns = 1 if rng.random() < 0.45 else rng.randint(2, 5)
            turns = [_pick(rng, week) for _ in range(n_turns)]
            if rng.random() < 0.7:
                _write_claude(claude, cwd, sid, start, turns, rng)
            else:
                _write_codex(codex, cwd, sid, start, turns)
    _write_hermes(home / ".hermes" / "state.db", rng)


def _write_claude(root, cwd, sid, start, turns, rng) -> None:
    folder = root / cwd.replace("/", "-")
    folder.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, text in enumerate(turns):
        # Mostly sonnet; the odd opus or haiku turn gives model-fit something to flag.
        r = rng.random()
        model = (
            "claude-opus-5-5"
            if r < 0.03
            else "claude-haiku-5-5" if r < 0.06 else ("claude-sonnet-5-5")
        )
        u = str(uuid.UUID(int=rng.getrandbits(128)))
        t = (start + timedelta(minutes=7 * i)).isoformat().replace("+00:00", "Z")
        lines.append(
            {
                "type": "user",
                "message": {"role": "user", "content": text},
                "promptSource": "sdk",
                "origin": {"kind": "human"},
                "isSidechain": False,
                "uuid": u,
                "timestamp": t,
                "sessionId": sid,
                "cwd": cwd,
            }
        )
        lines.append(
            {
                "type": "assistant",
                "parentUuid": u,
                "message": {"role": "assistant", "model": model, "content": "ok"},
                "timestamp": t,
                "sessionId": sid,
            }
        )
    (folder / f"{sid}.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))


def _write_codex(root, cwd, sid, start, turns) -> None:
    folder = root / start.strftime("%Y/%m/%d")
    folder.mkdir(parents=True, exist_ok=True)
    lines = [{"timestamp": start.isoformat(), "type": "session_meta", "payload": {"cwd": cwd}}]
    for i, text in enumerate(turns):
        t = (start + timedelta(minutes=6 * i)).isoformat()
        lines.append(
            {
                "timestamp": t,
                "type": "event_msg",
                "payload": {"type": "user_message", "kind": "plain", "message": text},
            }
        )
        lines.append({"timestamp": t, "type": "turn_context", "payload": {"model": "gpt-5.5"}})
    name = f"rollout-{start.strftime('%Y-%m-%dT%H-%M-%S')}-{sid}.jsonl"
    (folder / name).write_text("".join(json.dumps(x) + "\n" for x in lines))


def _write_hermes(db: Path, rng: random.Random) -> None:
    db.parent.mkdir(parents=True)
    conn = sqlite3.connect(db)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL,
            title TEXT, cwd TEXT, git_repo_root TEXT, model TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
            content TEXT, timestamp REAL, active INTEGER DEFAULT 1,
            compacted INTEGER DEFAULT 0);
        """)
    for week in range(12):
        for n in range(rng.randint(1, 3) + week // 3):
            sid = f"cron-{week}-{n}"
            ts = _ts(rng, week).timestamp()
            conn.execute(
                "INSERT INTO sessions VALUES (?, 'cron', ?, 'nightly', NULL, NULL, 'deepseek-v4')",
                (sid, ts),
            )
            conn.execute(
                "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
                (sid, "user", rng.choice(MACHINE), ts + 1),
            )
    conn.commit()
    conn.close()


def run(args: list[str], env: dict, stdin: str | None = None) -> str:
    result = subprocess.run(  # noqa: S603 -- our own CLI, fixed args
        [sys.executable, "-m", "prompt_coach", *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if result.returncode != 0:
        raise SystemExit(f"{args} failed:\n{result.stderr}")
    return result.stdout


def save(renderable, name: str, title: str) -> None:
    console = Console(record=True, width=WIDTH, force_terminal=True, file=io.StringIO())
    console.print(renderable)
    path = OUT / f"{name}.svg"
    console.save_svg(str(path), title=title)
    print(f"wrote {path.relative_to(ROOT)}")


def command(cmd: str, output: str) -> Group:
    prompt = Text.assemble(("~/projects/acme-api ", "bold blue"), ("$ ", "dim"), (cmd, "bold"))
    return Group(prompt, Text.from_ansi(output.rstrip("\n")))


def nudge_shot(env: dict, home: Path) -> None:
    prompt = "make the dashboard better"
    payload = {
        "hook_event_name": "UserPromptSubmit",
        "session_id": "demo-session",
        "prompt": prompt,
        "cwd": str(home / "projects" / "web-dashboard"),
    }
    # Real HOME for this one call: config.toml and its api_key_cmd live there.
    # Stores and cache stay pointed at the demo home via the env overrides.
    nudge_env = {**env, "HOME": str(Path.home()), "PROMPT_COACH_NUDGE_MODE": "always"}
    out = run(["nudge"], nudge_env, json.dumps(payload))
    if "reason" not in json.loads(out or "{}"):
        raise SystemExit("nudge did not block: is an LLM endpoint configured and reachable?")
    reason = json.loads(out)["reason"]
    rewrite = reason.split("\n\n")[1]
    # Laid out like Claude Code's transcript: the typed prompt, then the hook's
    # block message. The rewrite text is real LLM output for this prompt.
    body = Group(
        Text.assemble(("> ", "bold"), (prompt, "bold")),
        Text(""),
        Text.assemble(
            ("  ⎿  ", "dim"),
            ("UserPromptSubmit operation blocked by hook:", "bold yellow"),
        ),
        Padding(
            Group(
                Text("prompt-coach held this prompt back -- here's a tighter rewrite:"),
                Text(""),
                Padding(Text(rewrite, style="cyan"), (0, 0, 0, 2)),
                Text(""),
                Text.assemble(
                    "Reply ",
                    ("y", "bold green"),
                    " to send the rewrite, ",
                    ("n", "bold red"),
                    " to send your original (or run /prompt-coach:accept / "
                    "/prompt-coach:original).",
                ),
            ),
            (0, 0, 0, 5),
        ),
        Text(""),
        Panel(Text.assemble((">", "bold"), " y"), border_style="dim", width=WIDTH - 4),
    )
    save(body, "nudge", "Claude Code: prompt-coach nudge")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--nudge", action="store_true", help="also render the LLM nudge shot")
    opts = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp) / "home"
        build_home(home)
        env = {
            **os.environ,
            "HOME": str(home),
            "FORCE_COLOR": "1",
            "COLUMNS": str(WIDTH),
            "PROMPT_COACH_CLAUDE_PROJECTS": str(home / ".claude" / "projects"),
            "PROMPT_COACH_CODEX_DIR": str(home / ".codex" / "sessions"),
            "PROMPT_COACH_HERMES_DB": str(home / ".hermes" / "state.db"),
            "PROMPT_COACH_COPILOT_DIR": str(home / "no-copilot"),
            "PROMPT_COACH_ENABLED_STORES": "claude-code,codex,hermes",
            "PROMPT_COACH_CACHE_DIR": str(Path(tmp) / "cache"),
            "PROMPT_COACH_MODEL_FIT_MODE": "descriptive",
        }
        if not opts.nudge:
            # Deterministic shots never need the LLM: point it nowhere.
            env["XDG_CONFIG_HOME"] = str(Path(tmp) / "config")
        run(["cache", "sync"], env)
        save(
            command("prompt-coach dash", run(["dash", "--no-sync"], env)),
            "dash",
            "prompt-coach dash",
        )
        weak = 'prompt-coach score "fix the tests"'
        strong_text = STRONG[0]
        save(
            Group(
                command(weak, run(["score", "fix the tests"], env)),
                Text(""),
                command(
                    'prompt-coach score "In app/routes/orders.py, add a `status` filter..."',
                    run(["score", strong_text], env),
                ),
            ),
            "score",
            "prompt-coach score",
        )
        if opts.nudge:
            nudge_shot(env, home)


if __name__ == "__main__":
    main()
