"""Opt-in session-size warning in the live nudge (TEST-PLAN N1-N8)."""

import dataclasses
import json
import sqlite3
import time
from datetime import UTC, datetime

import pytest

from prompt_coach.cache import CacheDB
from prompt_coach.config import load_config
from prompt_coach.nudge import _record_nudge, cost_warning, with_cost_warning
from tests.test_usage_stores import _assistant, _hermes_db, _write_jsonl

PROMPT = "please refactor the whole billing module and tidy everything up"


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for name in ("PROMPT_COACH_NUDGE_COST_WARNING", "PROMPT_COACH_NUDGE_COST_WARNING_EVERY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("PROMPT_COACH_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PROMPT_COACH_HERMES_DB", str(tmp_path / "state.db"))
    return load_config(tmp_path / "nope.toml")


def _on(cfg, every=20):
    return dataclasses.replace(
        cfg, nudge=dataclasses.replace(cfg.nudge, cost_warning=True, cost_warning_every=every)
    )


def _transcript(tmp_path, calls, read):
    path = tmp_path / "proj" / "cc-1.jsonl"
    _write_jsonl(path, [_assistant(f"r{i}", read=read, out=100) for i in range(calls)])
    return path


def _payload(path, event="UserPromptSubmit"):
    return {
        "hook_event_name": event,
        "session_id": "cc-1",
        "prompt": PROMPT,
        "cwd": "/p/acme",
        "transcript_path": str(path),
    }


def test_off_by_default_and_never_looks(cfg, tmp_path, monkeypatch):
    assert cfg.nudge.cost_warning is False
    path = _transcript(tmp_path, 120, 150_000)

    def boom(*a, **k):
        raise AssertionError("looked up usage with the flag off")

    monkeypatch.setattr("prompt_coach.nudge._session_usage_now", boom)
    assert cost_warning(_payload(path), cfg) is None
    assert with_cost_warning({}, _payload(path), cfg) == {}


def test_small_session_gets_no_line(cfg, tmp_path):
    path = _transcript(tmp_path, 5, 20_000)
    assert cost_warning(_payload(path), _on(cfg)) is None


def test_big_session_gets_exactly_one_line_with_numbers(cfg, tmp_path):
    path = _transcript(tmp_path, 120, 150_000)
    line = cost_warning(_payload(path), _on(cfg))
    # last call: 150k read + 5 input + 300 cache writes ~ 150k context
    assert line.startswith("Session size: 120 calls, ~150k context, about USD ")
    assert "per reply (est.)" in line and "fresh session" in line
    assert "\n" not in line


def test_no_repeat_until_every_prompts(cfg, tmp_path):
    path = _transcript(tmp_path, 120, 150_000)
    c = _on(cfg, every=6)
    lines = [cost_warning(_payload(path), c) for _ in range(13)]
    fired = [i for i, line in enumerate(lines) if line]
    assert fired == [0, 6, 12]


def test_store_without_usage_is_silent(cfg, tmp_path):
    payload = _payload(tmp_path / "missing.jsonl")
    assert cost_warning(payload, _on(cfg)) is None
    payload.pop("transcript_path")
    assert cost_warning(payload, _on(cfg)) is None


def test_hermes_session_from_state_db(cfg, tmp_path):
    db = tmp_path / "state.db"
    _hermes_db(db)
    con = sqlite3.connect(db)
    con.execute("UPDATE session_model_usage SET api_call_count = 150")
    con.commit()
    con.close()
    payload = {"hook_event_name": "pre_llm_call", "session_id": "s1", "extra": {}}
    line = cost_warning(payload, _on(cfg, every=1))
    assert line and line.startswith("Session size: 600 calls")  # 4 rows x 150, sub1 folded in
    out = with_cost_warning({"context": "tip"}, payload, _on(cfg, every=1))
    assert out["context"].startswith("tip\nSession size:")


def test_lookup_failure_prints_nothing(cfg, tmp_path, monkeypatch):
    path = _transcript(tmp_path, 120, 150_000)

    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("prompt_coach.nudge._session_usage_now", broken)
    assert cost_warning(_payload(path), _on(cfg)) is None


def test_long_first_read_stays_quiet_until_caught_up(cfg, tmp_path, monkeypatch):
    path = _transcript(tmp_path, 120, 150_000)
    monkeypatch.setattr("prompt_coach.nudge._COST_BUDGET_S", -1.0)  # every slice is over budget
    assert cost_warning(_payload(path), _on(cfg)) is None
    monkeypatch.setattr("prompt_coach.nudge._COST_BUDGET_S", 10.0)
    assert cost_warning(_payload(path), _on(cfg)) is not None


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"systemMessage": "Tip: add an output format."},
        {"decision": "block", "reason": "held back -- rewrite below"},
        {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": "x"}},
    ],
)
def test_add_only_never_blocks_or_changes_the_response(cfg, tmp_path, response):
    path = _transcript(tmp_path, 120, 150_000)
    out = with_cost_warning(response, _payload(path), _on(cfg))
    for key, value in response.items():
        if key == "systemMessage":
            assert out[key].startswith(value + "\n")
        else:
            assert out[key] == value
    assert out.get("decision") == response.get("decision")
    assert "Session size:" in out["systemMessage"]


def test_other_events_never_get_a_line(cfg, tmp_path):
    path = _transcript(tmp_path, 120, 150_000)
    for event in ("Stop", "SessionStart"):
        assert cost_warning(_payload(path, event), _on(cfg)) is None


def test_record_nudge_keeps_the_cost_counters(cfg, tmp_path):
    path = _transcript(tmp_path, 120, 150_000)
    c = _on(cfg, every=3)
    assert cost_warning(_payload(path), c)
    _record_nudge(c.cache_dir, "other-session")
    assert cost_warning(_payload(path), c) is None  # counter survived: still throttled
    state = json.loads((c.cache_dir / "nudge_state.json").read_text())
    assert state["nudged_sessions"] == ["other-session"]
    assert "cc-1" in state["cost_warning_counts"]


def test_lookup_is_fast_on_a_big_cache(cfg, tmp_path):
    """N6: the session lookup itself on a 100k-row usage table."""
    cache = CacheDB(cfg.cache_dir / "cache.db")
    t0 = datetime(2026, 10, 1, tzinfo=UTC).timestamp()
    rows = [
        ("claude-code", f"s{i % 2000}", f"r{i}", t0 + i, t0 + i, "claude-sonnet-5", 1)
        + (10, 100_000, 0, 0, 100, "/p", 0, None)
        for i in range(100_000)
    ]
    cache.conn.executemany("INSERT INTO usage_records VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
    cache.conn.commit()
    start = time.perf_counter()
    records = cache.session_usage("claude-code", "s42")
    elapsed_ms = (time.perf_counter() - start) * 1000
    print(f"session lookup on 100k rows: {elapsed_ms:.1f} ms")
    assert len(records) == 50
    assert elapsed_ms < 250  # target 50 ms; generous for slow CI


def test_flag_round_trips_through_config(cfg, tmp_path, monkeypatch):
    from prompt_coach.config import write_config

    path = tmp_path / "config.toml"
    path.write_text("[nudge]\ncost_warning = true\ncost_warning_every = 7\n")
    loaded = load_config(path)
    assert (loaded.nudge.cost_warning, loaded.nudge.cost_warning_every) == (True, 7)
    write_config(loaded, tmp_path / "out.toml")
    again = load_config(tmp_path / "out.toml")
    assert (again.nudge.cost_warning, again.nudge.cost_warning_every) == (True, 7)
    monkeypatch.setenv("PROMPT_COACH_NUDGE_COST_WARNING", "0")
    assert load_config(path).nudge.cost_warning is False
