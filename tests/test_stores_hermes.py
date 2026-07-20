"""Hermes store reader tests against a minimal in-test clone of state.db.

The clone is built in the test source (no binary fixture) so the schema under
test stays visible and editable next to the assertions.
"""

import sqlite3
from datetime import UTC, datetime

import pytest

from prompt_coach.models import PromptOrigin
from prompt_coach.stores.hermes import HermesStore

T0 = datetime(2026, 7, 1, 12, 0, tzinfo=UTC).timestamp()


@pytest.fixture
def state_db(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, source TEXT, started_at REAL, title TEXT,
            cwd TEXT, git_repo_root TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
            timestamp REAL, active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0
        );
        """
    )
    conn.executemany(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?)",
        [
            ("s1", "cli", T0, "First session", "/home/x/proj", "/home/x/proj"),
            ("s2", "cli", T0 + 3600, "Second session", None, None),
        ],
    )
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp, active, compacted)"
        " VALUES (?,?,?,?,?,?)",
        [
            ("s1", "user", "how do I fix the login bug?", T0 + 10, 1, 0),
            ("s1", "assistant", "You could...", T0 + 20, 1, 0),
            ("s1", "tool", "{}", T0 + 30, 1, 0),
            ("s1", "user", "rewound message", T0 + 40, 0, 0),
            ("s1", "user", "compaction summary", T0 + 50, 1, 1),
            ("s2", "user", "TASK: build the CRUD endpoints", T0 + 3700, 1, 0),
            ("s2", "user", "", T0 + 3800, 1, 0),
        ],
    )
    conn.commit()
    conn.close()
    return path


def test_extracts_only_active_user_prompts(state_db):
    prompts = list(HermesStore(state_db).iter_prompts())
    contents = [p.content for p in prompts]
    assert contents == ["how do I fix the login bug?", "TASK: build the CRUD endpoints"]


def test_origin_classification(state_db):
    prompts = list(HermesStore(state_db).iter_prompts())
    assert prompts[0].origin is PromptOrigin.HUMAN
    assert prompts[1].origin is PromptOrigin.MACHINE


def test_timestamps_are_utc_aware(state_db):
    p = next(HermesStore(state_db).iter_prompts())
    assert p.timestamp.tzinfo is not None
    assert p.timestamp == datetime(2026, 7, 1, 12, 0, 10, tzinfo=UTC)


def test_session_context_carried(state_db):
    p = next(HermesStore(state_db).iter_prompts())
    assert p.cwd == "/home/x/proj"
    assert p.session_id == "s1"


def test_since_filter(state_db):
    since = datetime(2026, 7, 1, 13, 0, tzinfo=UTC)
    prompts = list(HermesStore(state_db).iter_prompts(since=since))
    assert [p.content for p in prompts] == ["TASK: build the CRUD endpoints"]


def test_discover_counts(state_db):
    info = HermesStore(state_db).discover()
    assert info.available
    assert info.session_count == 2
    assert info.prompt_count == 2


def test_missing_db_is_unavailable_not_fatal(tmp_path):
    store = HermesStore(tmp_path / "nope.db")
    info = store.discover()
    assert not info.available
    assert info.detail == "not found"
    assert list(store.iter_prompts()) == []


def test_source_db_never_written(state_db):
    before = state_db.read_bytes()
    list(HermesStore(state_db).iter_prompts())
    HermesStore(state_db).discover()
    assert state_db.read_bytes() == before
