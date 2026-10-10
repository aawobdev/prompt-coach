"""Usage readers and the usage cache (TEST-PLAN S1-S9, U10). Fixtures only."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from prompt_coach.analysis.cost import aggregate_sessions
from prompt_coach.cache import CacheDB
from prompt_coach.config import CostConfig
from prompt_coach.models import SourceKind
from prompt_coach.stores.chatgpt_export import ChatGPTExportStore
from prompt_coach.stores.claude_code import ClaudeCodeStore
from prompt_coach.stores.copilot_cli import CopilotCliStore
from prompt_coach.stores.hermes import HermesStore
from prompt_coach.stores.json_import import JsonImportStore

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC).timestamp()
SECRET_PROMPT = "please refactor the billing module for ACME-SECRET-7731"


def _hermes_db(path, *, with_usage=True):
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL,
            title TEXT, cwd TEXT, git_repo_root TEXT, model TEXT, parent_session_id TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
            content TEXT, timestamp REAL, active INTEGER DEFAULT 1,
            compacted INTEGER DEFAULT 0);
        """)
    conn.execute(
        "INSERT INTO sessions VALUES ('s1','cli',?, ?, '/p/acme', '/p/acme', NULL, NULL)",
        (T0, SECRET_PROMPT),
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('sub1','cli',?, 't', '/p/acme', NULL, NULL, 's1')", (T0,)
    )
    conn.execute(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES ('s1','user',?,?)",
        (SECRET_PROMPT, T0 + 1),
    )
    if with_usage:
        conn.executescript("""
            CREATE TABLE session_model_usage (
                session_id TEXT NOT NULL, model TEXT NOT NULL,
                billing_provider TEXT NOT NULL DEFAULT '', billing_base_url TEXT NOT NULL DEFAULT '',
                billing_mode TEXT NOT NULL DEFAULT '', task TEXT NOT NULL DEFAULT '',
                api_call_count INTEGER NOT NULL DEFAULT 0, input_tokens INTEGER NOT NULL DEFAULT 0,
                output_tokens INTEGER NOT NULL DEFAULT 0, cache_read_tokens INTEGER NOT NULL DEFAULT 0,
                cache_write_tokens INTEGER NOT NULL DEFAULT 0, reasoning_tokens INTEGER NOT NULL DEFAULT 0,
                estimated_cost_usd REAL NOT NULL DEFAULT 0, actual_cost_usd REAL NOT NULL DEFAULT 0,
                cost_status TEXT, cost_source TEXT, first_seen REAL, last_seen REAL,
                PRIMARY KEY (session_id, model, billing_provider, billing_base_url, billing_mode, task));
            """)
        rows = [
            # session, model, task, calls, in, out, read, write, est, status
            ("s1", "claude-sonnet-4-6", "", 40, 1_000_000, 0, 0, 0, 0.0, "unknown"),
            ("s1", "claude-opus-5", "", 10, 1_000_000, 0, 0, 0, 0.0, "unknown"),
            ("s1", "claude-haiku-4-5", "title_generation", 2, 1_000_000, 0, 0, 0, 0.0, "unknown"),
            ("sub1", "deepseek-v4-flash", "", 5, 0, 0, 0, 0, 0.25, "estimated"),
        ]
        for sid, model, task, calls, i, o, r, w, est, status in rows:
            conn.execute(
                "INSERT INTO session_model_usage (session_id, model, task, api_call_count,"
                " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,"
                " estimated_cost_usd, cost_status, first_seen, last_seen)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (sid, model, task, calls, i, o, r, w, est, status, T0, T0 + 600),
            )
    conn.commit()
    conn.close()


# -- Hermes (S1-S3) -------------------------------------------------------------


def test_hermes_overhead_and_subagents_fold_into_parent(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    records = list(HermesStore(db).iter_usage())
    assert {r.session_id for r in records} == {"s1"}  # sub1 folds into its parent
    (s,) = aggregate_sessions(records, CostConfig())
    # main rows: sonnet-4-6 $3 + opus-5 $5; title_generation haiku $1 + sub-agent $0.25
    assert s.est_cost == pytest.approx(9.25)
    assert s.overhead_cost == pytest.approx(1.25)
    assert s.overhead_calls == 7
    assert s.model == "claude-sonnet-4-6"


def test_hermes_opened_read_only(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    before = db.stat().st_mtime_ns
    store = HermesStore(db)
    list(store.iter_usage())
    with store._connect() as conn, pytest.raises(sqlite3.OperationalError):
        conn.execute("DELETE FROM session_model_usage")
    assert db.stat().st_mtime_ns == before


def test_hermes_without_usage_table_is_empty_not_an_error(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db, with_usage=False)
    store = HermesStore(db)
    assert list(store.iter_usage()) == []
    assert store.has_usage_table() is False


def test_hermes_since_filters_on_last_seen(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    later = datetime.fromtimestamp(T0 + 3600, tz=UTC)
    assert list(HermesStore(db).iter_usage(since=later)) == []


# -- Claude Code (S4-S6) ---------------------------------------------------------


def _assistant(req, *, out=10, read=0, side=False, session="cc-1", model="claude-sonnet-5"):
    return {
        "type": "assistant",
        "requestId": req,
        "sessionId": session,
        "timestamp": "2026-10-01T12:00:00Z",
        "cwd": "/p/acme",
        "isSidechain": side,
        "message": {
            "id": f"msg-{req}",
            "model": model,
            "content": [{"type": "text", "text": SECRET_PROMPT}],
            "usage": {
                "input_tokens": 5,
                "output_tokens": out,
                "cache_read_input_tokens": read,
                "cache_creation_input_tokens": 300,
                "cache_creation": {
                    "ephemeral_5m_input_tokens": 100,
                    "ephemeral_1h_input_tokens": 200,
                },
            },
        },
    }


def _write_jsonl(path, objs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(o) + "\n" for o in objs))


def _claude_records(store):
    out = {}
    for f in store.iter_usage_files():
        for _, r in store.iter_usage_file(f):
            out[(r.session_id, r.record_key)] = r  # same upsert rule as the cache
    return list(out.values())


def test_streamed_duplicates_count_once_with_final_numbers(tmp_path):
    _write_jsonl(
        tmp_path / "proj" / "cc-1.jsonl",
        [_assistant("r1", out=3), _assistant("r1", out=40), _assistant("r2", out=7)],
    )
    records = _claude_records(ClaudeCodeStore(tmp_path))
    assert len(records) == 2
    r1 = next(r for r in records if r.record_key == "r1")
    assert r1.output_tokens == 40
    assert (r1.cache_write_tokens, r1.cache_write_1h_tokens) == (100, 200)


def test_lines_without_usage_are_skipped(tmp_path):
    no_usage = {"type": "assistant", "requestId": "x", "sessionId": "cc-1", "message": {}}
    synthetic = _assistant("syn", model="<synthetic>")
    user = {"type": "user", "message": {"content": SECRET_PROMPT}, "sessionId": "cc-1"}
    _write_jsonl(tmp_path / "proj" / "cc-1.jsonl", [no_usage, synthetic, user, _assistant("r1")])
    assert [r.record_key for r in _claude_records(ClaudeCodeStore(tmp_path))] == ["r1"]


def test_sidechain_and_subagent_files_fold_in_as_overhead(tmp_path):
    """DECISIONS.md 2026-10-10: sub-agent calls are real spend on the parent
    session, folded in and reported as overhead."""
    _write_jsonl(tmp_path / "proj" / "cc-1.jsonl", [_assistant("r1"), _assistant("r2", side=True)])
    _write_jsonl(
        tmp_path / "proj" / "cc-1" / "subagents" / "agent-a1.jsonl",
        [_assistant("r3", side=False)],
    )
    records = _claude_records(ClaudeCodeStore(tmp_path))
    assert {r.session_id for r in records} == {"cc-1"}
    assert sorted((r.record_key, r.overhead) for r in records) == [
        ("r1", False),
        ("r2", True),
        ("r3", True),
    ]


def test_prompt_reader_still_ignores_subagent_files(tmp_path):
    _write_jsonl(tmp_path / "proj" / "cc-1" / "subagents" / "agent-a1.jsonl", [_assistant("r")])
    assert list(ClaudeCodeStore(tmp_path).iter_files()) == []


# -- stores without usage (S7) ---------------------------------------------------


def test_stores_without_usage_have_no_usage_methods(tmp_path):
    for store in (ChatGPTExportStore(tmp_path / "x.zip"), JsonImportStore(tmp_path / "x.json")):
        assert not hasattr(store, "iter_usage") and not hasattr(store, "iter_usage_file")


# -- Copilot CLI (unverified format) ----------------------------------------------


def test_copilot_cli_last_total_per_model(tmp_path):
    events = [
        {
            "type": "session.start",
            "timestamp": "2026-10-01T12:00:00Z",
            "data": {"context": {"cwd": "/p/acme"}},
        },
        {"type": "user.message", "data": {"content": SECRET_PROMPT}},
        {
            "type": "session.usage_checkpoint",
            "timestamp": "2026-10-01T12:10:00Z",
            "data": {"modelMetrics": {"claude-sonnet-4.6": {"usage": {"inputTokens": 1}}}},
        },
        {
            "type": "session.shutdown",
            "timestamp": "2026-10-01T12:30:00Z",
            "data": {
                "modelMetrics": {
                    "claude-sonnet-4.6": {
                        "usage": {
                            "inputTokens": 1000,
                            "outputTokens": 50,
                            "cacheReadTokens": 600,
                            "cacheWriteTokens": 100,
                        },
                        "requests": {"count": 4, "cost": 1},
                    }
                }
            },
        },
    ]
    _write_jsonl(tmp_path / "sess-1" / "events.jsonl", events)
    (r,) = list(CopilotCliStore(tmp_path).iter_usage())
    assert (r.session_id, r.model, r.calls, r.cwd) == ("sess-1", "claude-sonnet-4.6", 4, "/p/acme")
    assert (r.input_tokens, r.cache_read_tokens, r.cache_write_tokens) == (300, 600, 100)
    assert list(CopilotCliStore(tmp_path).iter_prompts()) == []


# -- cache: incremental sync (S8), migration (U10), privacy (S9) -----------------


def test_incremental_sync_adds_without_double_counting(tmp_path):
    transcript = tmp_path / "projects" / "proj" / "cc-1.jsonl"
    _write_jsonl(transcript, [_assistant("r1"), _assistant("r1")])
    store = ClaudeCodeStore(tmp_path / "projects")
    cache = CacheDB(tmp_path / "cache.db")
    cache.sync([store])
    assert sum(r.calls for r in cache.usage_records()) == 1
    with open(transcript, "a") as f:
        f.write(json.dumps(_assistant("r1", out=99)) + "\n")  # late duplicate
        f.write(json.dumps(_assistant("r2")) + "\n")
    cache.sync([store])
    records = cache.usage_records()
    assert sorted(r.record_key for r in records) == ["r1", "r2"]
    assert next(r for r in records if r.record_key == "r1").output_tokens == 99


def test_usage_sync_can_be_turned_off(tmp_path):
    _write_jsonl(tmp_path / "projects" / "proj" / "cc-1.jsonl", [_assistant("r1")])
    cache = CacheDB(tmp_path / "cache.db")
    cache.sync([ClaudeCodeStore(tmp_path / "projects")], usage=False)
    assert cache.usage_records() == []


def test_usage_table_added_to_an_old_cache(tmp_path):
    path = tmp_path / "cache.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE prompts (
            source TEXT NOT NULL, session_id TEXT NOT NULL, message_ref TEXT NOT NULL,
            content TEXT NOT NULL, content_hash TEXT NOT NULL, timestamp REAL NOT NULL,
            origin TEXT NOT NULL, cwd TEXT, git_repo TEXT,
            PRIMARY KEY (source, session_id, message_ref));
        INSERT INTO prompts VALUES ('hermes','s','m','old row','h',1.0,'human',NULL,NULL);
        """)
    conn.commit()
    conn.close()
    cache = CacheDB(path)
    assert cache.usage_records() == []
    assert cache.counts()["prompts"] == 1


def test_no_prompt_text_reaches_usage_records_or_cache(tmp_path):
    hermes = tmp_path / "state.db"
    _hermes_db(hermes)
    _write_jsonl(tmp_path / "projects" / "proj" / "cc-1.jsonl", [_assistant("r1")])
    cache = CacheDB(tmp_path / "cache.db")
    cache.sync([HermesStore(hermes), ClaudeCodeStore(tmp_path / "projects")])
    records = cache.usage_records()
    assert records
    assert SECRET_PROMPT not in repr(records)
    rows = cache.conn.execute("SELECT * FROM usage_records").fetchall()
    assert all(SECRET_PROMPT not in str(tuple(r)) for r in rows)


def test_window_keeps_only_recent_records(tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    cache = CacheDB(tmp_path / "cache.db")
    cache.sync([HermesStore(db)])
    soon = datetime.fromtimestamp(T0, tz=UTC) + timedelta(days=1)
    assert cache.usage_records(since=soon) == []
    assert {r.source for r in cache.usage_records()} == {SourceKind.HERMES}
