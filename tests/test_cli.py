"""CLI tests via typer's CliRunner, with stores and cache redirected to tmp dirs."""

import json
import sqlite3
from datetime import UTC, datetime

import pytest
import typer
from typer.testing import CliRunner

from prompt_coach.cli import app, parse_since

runner = CliRunner()

T0 = datetime(2026, 7, 1, 12, 0, tzinfo=UTC).timestamp()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect config to tmp fixtures: a tiny hermes clone, a tiny claude tree,
    an isolated cache, and an unreachable LLM endpoint (degraded mode)."""
    hermes = tmp_path / "state.db"
    conn = sqlite3.connect(hermes)
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL,
            title TEXT, cwd TEXT, git_repo_root TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
            content TEXT, timestamp REAL, active INTEGER DEFAULT 1,
            compacted INTEGER DEFAULT 0);
        """
    )
    conn.execute("INSERT INTO sessions VALUES ('s1','cli',?, 'Fix nginx', '/p', '/p')", (T0,))
    conn.executemany(
        "INSERT INTO messages (session_id, role, content, timestamp) VALUES (?,?,?,?)",
        [
            ("s1", "user", "why does nginx return 502 for long requests?", T0 + 1),
            ("s1", "user", "TASK: write the nginx config\nVerify: reload passes", T0 + 2),
        ],
    )
    conn.commit()
    conn.close()

    claude = tmp_path / "projects" / "-p"
    claude.mkdir(parents=True)
    (claude / "sess.jsonl").write_text(
        json.dumps(
            {
                "type": "user",
                "message": {"role": "user", "content": "add a retry to the fetcher"},
                "promptSource": "typed",
                "origin": {"kind": "human"},
                "isSidechain": False,
                "uuid": "u-1",
                "timestamp": "2026-07-01T13:00:00.000Z",
                "sessionId": "cc-1",
                "cwd": "/p",
            }
        )
        + "\n"
    )

    monkeypatch.setenv("PROMPT_COACH_HERMES_DB", str(hermes))
    monkeypatch.setenv("PROMPT_COACH_CLAUDE_PROJECTS", str(tmp_path / "projects"))
    monkeypatch.setenv("PROMPT_COACH_COPILOT_DIR", str(tmp_path / "copilot"))  # keep off /mnt/c
    monkeypatch.setenv("PROMPT_COACH_CODEX_DIR", str(tmp_path / "codex"))  # keep off /mnt/c
    monkeypatch.setenv("PROMPT_COACH_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("PROMPT_COACH_API_BASE", "http://127.0.0.1:9")  # nothing listens
    return tmp_path


def test_parse_since():
    assert parse_since(None) is None
    assert parse_since("2026-07-01").day == 1
    delta = datetime.now(tz=UTC) - parse_since("7d")
    assert 6.9 < delta.days + delta.seconds / 86400 < 7.1
    with pytest.raises(typer.BadParameter):
        parse_since("nonsense")


def test_discover_lists_stores(env):
    result = runner.invoke(app, ["discover"])
    assert result.exit_code == 0
    assert "hermes" in result.output
    assert "claude-code" in result.output
    assert "available" in result.output


def test_stats(env):
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "Refinement rate" in result.output
    assert "Human" in result.output and "Machine" in result.output


def test_report_degraded(env):
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 0
    assert "LLM unavailable - deterministic analysis only" in result.output
    assert "## Style Profile" in result.output
    assert "## Rubric Scorecard" in result.output  # deterministic rules still scored


def test_report_out_file(env):
    out = env / "report.md"
    result = runner.invoke(app, ["report", "--out", str(out)])
    assert result.exit_code == 0
    assert out.is_file()
    assert "# Prompt Coach Report" in out.read_text()


def test_report_empty_range_is_not_an_error(env):
    result = runner.invoke(app, ["report", "--since", "2030-01-01"])
    assert result.exit_code == 0
    assert "quiet week" in result.output


def test_query_degraded_shows_snippets(env):
    result = runner.invoke(app, ["query", "nginx"])
    assert result.exit_code == 0
    assert "LLM unavailable" in result.output
    assert "nginx" in result.output


def test_nudge_hook_long_weak_prompt(env):
    payload = json.dumps(
        {
            "prompt": "please help me redesign the whole reporting pipeline. " * 5,
            "session_id": "hook-s1",
        }
    )
    result = runner.invoke(app, ["nudge"], input=payload)
    assert result.exit_code == 0
    body = json.loads(result.output)
    assert "systemMessage" in body


def test_nudge_hook_short_prompt_is_silent(env):
    payload = json.dumps({"prompt": "run it", "session_id": "hook-s1"})
    result = runner.invoke(app, ["nudge"], input=payload)
    assert result.exit_code == 0
    assert json.loads(result.output) == {}


def test_nudge_hook_malformed_stdin_never_crashes(env):
    result = runner.invoke(app, ["nudge"], input="not json")
    assert result.exit_code == 0
    assert json.loads(result.output) == {}


def test_nudge_hook_stop_event_reads_transcript(env):
    transcript = env / "sess.jsonl"
    transcript.write_text(
        json.dumps(
            {
                "type": "user",
                "message": {
                    "role": "user",
                    "content": "please help me redesign the whole reporting pipeline. " * 5,
                },
                "promptSource": "typed",
                "origin": {"kind": "human"},
                "isSidechain": False,
                "uuid": "u-1",
                "timestamp": "2026-07-27T10:00:00.000Z",
                "sessionId": "hook-s2",
                "cwd": "/p",
            }
        )
        + "\n"
    )
    payload = json.dumps(
        {
            "hook_event_name": "Stop",
            "session_id": "hook-s2",
            "transcript_path": str(transcript),
        }
    )
    result = runner.invoke(app, ["nudge"], input=payload)
    assert result.exit_code == 0
    assert "systemMessage" in json.loads(result.output)


def test_nudge_hook_off_mode_is_silent(env, monkeypatch):
    monkeypatch.setenv("PROMPT_COACH_NUDGE_MODE", "off")
    payload = json.dumps(
        {
            "prompt": "please help me redesign the whole reporting pipeline. " * 5,
            "session_id": "hook-s3",
        }
    )
    result = runner.invoke(app, ["nudge"], input=payload)
    assert result.exit_code == 0
    assert json.loads(result.output) == {}


def test_nudge_hook_always_mode_blocks_with_llm_rewrite(env, monkeypatch):
    monkeypatch.setenv("PROMPT_COACH_NUDGE_MODE", "always")
    monkeypatch.setattr("prompt_coach.nudge._make_llm", lambda cfg: object())
    monkeypatch.setattr("prompt_coach.nudge.rewrite_prompt", lambda prompt, llm: "REWRITTEN")
    payload = json.dumps({"prompt": "add a retry to the fetcher", "session_id": "hook-s4"})
    result = runner.invoke(app, ["nudge"], input=payload)
    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["decision"] == "block"
    assert "REWRITTEN" in body["reason"]


def test_import_roundtrip(env, sample_sessions_path):
    result = runner.invoke(app, ["import", str(sample_sessions_path)])
    assert result.exit_code == 0
    assert "Imported" in result.output
    # Re-import dedupes everything.
    again = runner.invoke(app, ["import", str(sample_sessions_path)])
    assert "Imported 0 prompts" in again.output


def test_import_missing_file(env):
    result = runner.invoke(app, ["import", str(env / "nope.json")])
    assert result.exit_code == 1


def test_cache_info_counts_only(env):
    runner.invoke(app, ["cache", "sync"])
    result = runner.invoke(app, ["cache", "info"])
    assert result.exit_code == 0
    assert "prompts" in result.output
    assert "nginx" not in result.output  # never prints content


def test_cache_clear(env):
    runner.invoke(app, ["cache", "sync"])
    result = runner.invoke(app, ["cache", "clear", "--yes"])
    assert result.exit_code == 0
    info = runner.invoke(app, ["cache", "info"])
    assert "prompts: 0" in info.output


def test_serve_is_phase_2(env):
    assert runner.invoke(app, ["serve"]).exit_code == 1


def test_dash_smoke(env):
    result = runner.invoke(app, ["dash", "--plain"])
    assert result.exit_code == 0
    assert "nginx" not in result.output  # no prompt content on screen


def test_dash_quiet_week_is_not_an_error(env):
    result = runner.invoke(app, ["dash", "--plain", "--since", "2030-01-01"])
    assert result.exit_code == 0
    assert "quiet week" in result.output


def test_stats_quiet_week_is_not_an_error(env):
    result = runner.invoke(app, ["stats", "--since", "2030-01-01"])
    assert result.exit_code == 0
    assert "quiet week" in result.output


def test_stats_shows_no_llm_tag(env):
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "no LLM" in result.output


def test_dash_no_sync_skips_sync(env, monkeypatch):
    calls = []
    monkeypatch.setattr("prompt_coach.cache.CacheDB.sync", lambda self, *a, **k: calls.append(1))

    result = runner.invoke(app, ["dash", "--plain", "--no-sync"])
    assert result.exit_code == 0  # nothing in cache yet, never synced -- not an error (D7)
    assert "no cache yet" in result.output
    assert calls == []

    runner.invoke(app, ["dash", "--plain"])
    assert calls == [1]
