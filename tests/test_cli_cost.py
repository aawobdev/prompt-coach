"""`prompt-coach cost` and the doctor cost checks (TEST-PLAN C1-C6, C9)."""

import json
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from prompt_coach.cli import app
from tests.test_usage_stores import SECRET_PROMPT, _assistant, _hermes_db, _write_jsonl

runner = CliRunner()
GOLDEN = Path(__file__).parent / "fixtures" / "cost_plain.golden"


@pytest.fixture
def env(tmp_path, monkeypatch):
    hermes = tmp_path / "state.db"
    _hermes_db(hermes)
    projects = tmp_path / "projects"
    big = [_assistant(f"r{i}", read=150_000, out=2_000) for i in range(6)]
    _write_jsonl(projects / "acme" / "cc-1.jsonl", big)
    small = [
        _assistant(f"q{i}", session="cc-2", model="claude-opus-5-5", out=1_000) for i in range(3)
    ]
    _write_jsonl(projects / "web" / "cc-2.jsonl", small)
    _write_jsonl(
        projects / "web" / "cc-3.jsonl", [_assistant("u1", session="cc-3", model="gpt-oss")]
    )
    # a prompt-only session (no usage) so the "No usage data" line has something to say
    _write_jsonl(
        projects / "web" / "cc-4.jsonl",
        [
            {
                "type": "user",
                "message": {"role": "user", "content": "add a retry to the fetcher please"},
                "promptSource": "sdk",
                "origin": {"kind": "human"},
                "isSidechain": False,
                "uuid": "u-4",
                "timestamp": "2026-10-01T12:00:00Z",
                "sessionId": "cc-4",
                "cwd": "/p/web",
            }
        ],
    )
    for name, value in {
        "PROMPT_COACH_HERMES_DB": hermes,
        "PROMPT_COACH_CLAUDE_PROJECTS": projects,
        "PROMPT_COACH_COPILOT_DIR": tmp_path / "copilot",
        "PROMPT_COACH_CODEX_DIR": tmp_path / "codex",
        "PROMPT_COACH_COPILOT_CLI_DIR": tmp_path / "copilot-cli",
        "PROMPT_COACH_CACHE_DIR": tmp_path / "cache",
        "XDG_CONFIG_HOME": tmp_path / "xdg",
        "PROMPT_COACH_CLAUDE_HOME": tmp_path / "claude_home",
    }.items():
        monkeypatch.setenv(name, str(value))
    monkeypatch.setenv("PROMPT_COACH_ENABLED_STORES", "hermes,claude-code")
    monkeypatch.setattr("prompt_coach.plugin.find_repo_root", lambda: None)
    return tmp_path


def _cost(*args):
    result = runner.invoke(app, ["cost", "--since", "2026-09-01", *args])
    assert result.exit_code == 0, result.output
    return result.output


def test_plain_output_matches_golden_file(env):
    out = _cost("--plain")
    if os.environ.get("PROMPT_COACH_UPDATE_GOLDEN"):  # review the diff before committing
        GOLDEN.write_text(out)
    assert GOLDEN.exists(), "missing golden: rerun with PROMPT_COACH_UPDATE_GOLDEN=1"
    assert out == GOLDEN.read_text()


def test_json_is_valid_and_has_no_prompt_text(env):
    out = _cost("--json")
    data = json.loads(out)
    assert data["params"]["billed_fraction"] == 1.0
    assert data["totals"]["sessions"] == len(data["sessions"]) == 4
    costs = [s["est_cost"] or 0 for s in data["sessions"]]
    assert costs == sorted(costs, reverse=True)
    assert data["unpriced_models"] == {"gpt-oss": 1}
    assert SECRET_PROMPT not in out


@pytest.mark.parametrize("by", ["folder", "model", "store", "day"])
def test_grouped_totals_equal_the_session_total(env, by):
    total = json.loads(_cost("--json"))["totals"]["est_cost"]
    groups = json.loads(_cost("--json", "--by", by))["groups"]
    assert sum(g["est_cost"] or 0 for g in groups) == pytest.approx(total, abs=1e-3)


def test_window_excludes_older_sessions_and_header_states_it(env):
    out = runner.invoke(app, ["cost", "--since", "2026-10-02", "--plain"]).output
    assert "since 2026-10-02" in out
    assert "no usage data" in out


def test_no_usage_anywhere_is_a_friendly_empty_state(env, monkeypatch, tmp_path):
    monkeypatch.setenv("PROMPT_COACH_ENABLED_STORES", "codex")
    result = runner.invoke(app, ["cost", "--plain"])
    assert result.exit_code == 0
    assert "no usage data" in result.output


def test_header_always_has_disclaimer_and_fraction(env, monkeypatch):
    monkeypatch.setenv("PROMPT_COACH_COST_BILLED_FRACTION", "0.7")
    out = _cost("--plain")
    assert "Estimates from list prices; not a bill." in out
    assert "list x 0.7" in out
    rich_out = _cost()
    assert "not a bill" in rich_out


def test_source_filter(env):
    data = json.loads(_cost("--json", "--source", "hermes"))
    assert {s["source"] for s in data["sessions"]} == {"hermes"}


def test_cost_disabled(env, monkeypatch):
    monkeypatch.setenv("PROMPT_COACH_COST_ENABLED", "false")
    result = runner.invoke(app, ["cost"])
    assert result.exit_code == 0
    assert "off" in result.output


def test_bad_by_is_rejected(env):
    assert runner.invoke(app, ["cost", "--by", "colour"]).exit_code != 0


def test_doctor_cost_checks(env):
    runner.invoke(app, ["cache", "sync"])
    out = runner.invoke(app, ["doctor"]).output
    assert "billed_fraction is 1.0" in out
    assert "models with no price: gpt-oss" in out
    assert "hermes usage: 1 session readable" in out
    data = json.loads(runner.invoke(app, ["doctor", "--json"]).output)
    assert any(c["message"].startswith("price table 2026-10-06") for c in data["cost"])
    assert {c["level"] for c in data["cost"]} <= {"ok", "warn"}


def test_doctor_flags_a_stale_price_table(env, tmp_path):
    cfg = tmp_path / "xdg" / "prompt-coach" / "config.toml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text('[cost]\nprice_table_date = "2025-01-01"\nbilled_fraction = 0.5\n')
    out = runner.invoke(app, ["doctor"]).output
    assert "[warn]  price table 2025-01-01" in out
    assert "billed_fraction is 1.0" not in out
