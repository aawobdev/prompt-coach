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
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, source TEXT, started_at REAL, title TEXT,
            cwd TEXT, git_repo_root TEXT, model TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
            timestamp REAL, active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0
        );
        """)
    conn.executemany(
        "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
        [
            ("s1", "cli", T0, "First session", "/home/x/proj", "/home/x/proj", "qwen3-coder:30b"),
            ("s2", "cli", T0 + 3600, "Second session", None, None, None),
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


def test_model_captured_session_level(state_db):
    prompts = list(HermesStore(state_db).iter_prompts())
    assert prompts[0].model == "qwen3-coder:30b"
    assert prompts[1].model is None


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


# -- gateway wrappers and machine rows ----------------------------------------
# Shapes copied from the live state.db 2026-09-24, with the text swapped out.
# Each case is built so only the rule it names decides the outcome.


def _db(tmp_path, rows):
    """state.db clone holding (session source, content) user rows, in order."""
    path = tmp_path / "wrapped.db"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY, source TEXT, started_at REAL, title TEXT,
            cwd TEXT, git_repo_root TEXT, model TEXT
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
            timestamp REAL, active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0
        );
        """)
    for i, (source, content) in enumerate(rows):
        conn.execute(
            "INSERT INTO sessions VALUES (?,?,?,?,?,?,?)",
            (f"s{i}", source, T0, None, None, None, None),
        )
        conn.execute(
            "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
            (f"s{i}", "user", content, T0 + i),
        )
    conn.commit()
    conn.close()
    return path


def _read(tmp_path, rows):
    return [(p.content, p.origin) for p in HermesStore(_db(tmp_path, rows)).iter_prompts()]


HUMAN = PromptOrigin.HUMAN
MACHINE = PromptOrigin.MACHINE


@pytest.mark.parametrize("source", ["cron", "subagent"])
def test_cron_and_subagent_sessions_are_machine(tmp_path, source):
    assert _read(tmp_path, [(source, "check the disks")]) == [("check the disks", MACHINE)]


def test_slack_and_cli_sessions_are_human(tmp_path):
    rows = [("slack", "check the disks"), ("cli", "check the logs")]
    assert _read(tmp_path, rows) == [("check the disks", HUMAN), ("check the logs", HUMAN)]


def test_replying_to_quote_is_stripped(tmp_path):
    raw = '[Replying to: "Done. The backup ran\n\nand finished."]\n\nnow do the nas'
    assert _read(tmp_path, [("slack", raw)]) == [("now do the nas", HUMAN)]


def test_thread_context_is_stripped(tmp_path):
    raw = (
        "[Thread context \u2014 prior messages in this thread (not yet in conversation):]\n"
        "[thread parent] Hermes: the sync failed\n"
        "[End of thread context]\n\n"
        "retry it with verbose logging"
    )
    assert _read(tmp_path, [("slack", raw)]) == [("retry it with verbose logging", HUMAN)]


def test_model_switch_note_is_stripped(tmp_path):
    raw = "[Note: model was just switched from a to b.]\n\nsummarise the diff"
    assert _read(tmp_path, [("cli", raw)]) == [("summarise the diff", HUMAN)]


def test_attachment_notice_is_stripped_by_bracket_balance(tmp_path):
    # The vision description spans lines and holds its own [brackets].
    raw = (
        "[The user sent an image~ Here's what I can see:\n"
        "A dashboard with a red [ERROR] badge.\n\nTwo graphs.]\n"
        "[If you need a closer look, use vision_analyze with image_url: /tmp/x.png]\n\n"
        "why is this red"
    )
    assert _read(tmp_path, [("slack", raw)]) == [("why is this red", HUMAN)]


def test_attachment_only_row_is_skipped(tmp_path):
    raw = "[The user sent a document: 'a.pdf'. It is saved at: /tmp/a.pdf]"
    assert _read(tmp_path, [("slack", raw)]) == []


def test_unclosed_notice_is_machine_not_misread(tmp_path):
    raw = "[The user sent an image~ Here's what I can see:\nno closing bracket"
    assert _read(tmp_path, [("slack", raw)]) == [(raw, MACHINE)]


def test_owner_speaker_label_is_stripped(tmp_path):
    assert _read(tmp_path, [("slack", "[sam] restart plex")]) == [("restart plex", HUMAN)]


def test_other_speakers_messages_are_skipped(tmp_path):
    rows = [
        ("slack", "[sam] restart plex"),
        ("slack", "[sam] and sonarr"),
        ("slack", "[robin] what about radarr"),
    ]
    assert _read(tmp_path, rows) == [("restart plex", HUMAN), ("and sonarr", HUMAN)]


def test_document_header_is_not_a_speaker(tmp_path):
    raw = "[Content of notes.txt]:\nline one"
    assert _read(tmp_path, [("slack", raw)]) == [(raw, HUMAN)]


def test_injected_skill_body_is_stripped(tmp_path):
    raw = (
        '[sam] [IMPORTANT: The "homelab" skill is auto-loaded. Follow its'
        " instructions for this session.]\n\n---\nname: homelab\n---\n# Homelab\n"
        "Step: do things.\n[Skill directory: /home/x/skills/homelab]\n"
        "Resolve relative paths, then run them with the terminal tool using the"
        " absolute path.\n\nadd the documentary to radarr"
    )
    assert _read(tmp_path, [("slack", raw)]) == [("add the documentary to radarr", HUMAN)]


def test_skill_block_without_its_closing_sentence_is_machine(tmp_path):
    raw = '[IMPORTANT: The "homelab" skill is auto-loaded.]\n\n---\nname: homelab'
    assert _read(tmp_path, [("slack", raw)]) == [(raw, MACHINE)]


def test_stacked_wrappers_all_stripped(tmp_path):
    raw = (
        '[Replying to: "Which host?"]\n\n[sam] '
        "[The user sent a document: 'a.log'. It is saved at: /tmp/a.log]\n\n"
        "the media host"
    )
    assert _read(tmp_path, [("slack", raw)]) == [("the media host", HUMAN)]


@pytest.mark.parametrize(
    "raw",
    [
        "[System: The previous response was cut off by a network error.]",
        "[IMPORTANT: Background process proc_1 completed normally (exit code 0).]",
        "[CONTEXT COMPACTION \u2014 REFERENCE ONLY] Earlier turns were compacted.",
        "[PRIOR CONTEXT \u2014 for reference only; not a new message]",
        "[ASYNC DELEGATION BATCH COMPLETE \u2014 deleg_1]",
    ],
)
def test_agent_notices_are_machine(tmp_path, raw):
    assert _read(tmp_path, [("slack", raw)]) == [(raw, MACHINE)]


def test_notice_behind_a_reply_quote_is_still_machine(tmp_path):
    raw = '[Replying to: "ok"]\n\n[System: Your previous tool call was too large.]'
    assert _read(tmp_path, [("slack", raw)]) == [(raw, MACHINE)]


def test_card_specs_are_machine(tmp_path):
    raw = "CARD 1 of 2. Wire the stale badge into the project cards."
    assert _read(tmp_path, [("cli", raw)]) == [(raw, MACHINE)]
