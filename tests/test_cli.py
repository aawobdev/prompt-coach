"""CLI tests via typer's CliRunner, with stores and cache redirected to tmp dirs."""

import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
import typer
from typer.testing import CliRunner

from prompt_coach.cli import app, parse_since

runner = CliRunner()

# Relative to "now", not a fixed calendar date: dash/serve default to a 12w
# window (see test_serve_dashboard_server_responds and
# test_dash_html_writes_standalone_page), and a fixed date silently ages out
# of that window and starts failing once 12 weeks pass -- confirmed live
# 2026-09-24, when a fixed 2026-07-01 fixture crossed the cliff (STATUS.md).
_NOW = datetime.now(tz=UTC)
T0 = (_NOW - timedelta(days=1)).timestamp()
_T0_ISO = (_NOW - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Redirect config to tmp fixtures: a tiny hermes clone, a tiny claude tree,
    an isolated cache, and an unreachable LLM endpoint (degraded mode)."""
    hermes = tmp_path / "state.db"
    conn = sqlite3.connect(hermes)
    conn.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, started_at REAL,
            title TEXT, cwd TEXT, git_repo_root TEXT, model TEXT);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT,
            content TEXT, timestamp REAL, active INTEGER DEFAULT 1,
            compacted INTEGER DEFAULT 0);
        """
    )
    conn.execute("INSERT INTO sessions VALUES ('s1','cli',?, 'Fix nginx', '/p', '/p', NULL)", (T0,))
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
                "timestamp": _T0_ISO,
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
    # Isolates config.toml reads/writes from the real machine's
    # ~/.config/prompt-coach/config.toml -- load_config() falls back to
    # XDG_CONFIG_HOME when no explicit path is given, and `setup` writes
    # there, so this must be redirected for every CLI test, not just setup's.
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    # Isolates doctor/migrate-legacy from the real ~/.claude -- see
    # cli.py's _claude_home().
    monkeypatch.setenv("PROMPT_COACH_CLAUDE_HOME", str(tmp_path / "claude_home"))
    # find_repo_root() reads uv's own tool receipt, which is real machine
    # state (dev has prompt-coach uv-tool-installed for real) -- default it
    # to "not found" like a clean machine, so setup's plugin-offer step and
    # any test that doesn't care about repo-root discovery stays
    # deterministic. Tests that DO care override this themselves.
    monkeypatch.setattr("prompt_coach.plugin.find_repo_root", lambda: None)
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


def test_nudge_hook_session_start_fresh_install_is_silent(env):
    payload = json.dumps({"hook_event_name": "SessionStart", "source": "startup"})
    result = runner.invoke(app, ["nudge"], input=payload)
    assert result.exit_code == 0
    assert json.loads(result.output) == {}  # JSON, never plain text (which reaches the model)


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
    monkeypatch.setattr(
        "prompt_coach.nudge.rewrite_prompt", lambda prompt, llm, context=None: "REWRITTEN"
    )
    payload = json.dumps({"prompt": "add a retry to the fetcher", "session_id": "hook-s4"})
    result = runner.invoke(app, ["nudge"], input=payload)
    assert result.exit_code == 0
    body = json.loads(result.output)
    assert body["decision"] == "block"
    assert "REWRITTEN" in body["reason"]


def test_nudge_consume_prints_rewritten_side(env, monkeypatch):
    monkeypatch.setattr("prompt_coach.nudge._make_llm", lambda cfg: object())
    monkeypatch.setattr(
        "prompt_coach.nudge.rewrite_prompt", lambda prompt, llm, context=None: "REWRITTEN"
    )
    payload = json.dumps(
        {
            "prompt": "please help me redesign the whole reporting pipeline. " * 5,
            "session_id": "hook-s5",
        }
    )
    runner.invoke(app, ["nudge"], input=payload)  # blocks, stashes the pending rewrite
    result = runner.invoke(app, ["nudge-consume", "--want", "rewritten", "--session", "hook-s5"])
    assert result.exit_code == 0
    assert result.output.strip() == "REWRITTEN"


def test_nudge_consume_nothing_pending_is_not_an_error(env):
    result = runner.invoke(app, ["nudge-consume", "--want", "rewritten", "--session", "no-such"])
    assert result.exit_code == 0
    assert "no prompt-coach rewrite is pending" in result.output


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


def test_serve_dashboard_server_responds(env):
    """Build the server (not the blocking `serve` command), hit it in a thread,
    and confirm it returns the standalone dashboard page with no prompt content."""
    import threading
    import urllib.error
    import urllib.request

    from prompt_coach.cli import _build_dash_server, default_stores, open_cache
    from prompt_coach.config import load_config

    cfg = load_config()
    open_cache(cfg).sync(default_stores(cfg))  # populate the cache first
    server = _build_dash_server(cfg, "127.0.0.1", 0, "12w", {})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        with urllib.request.urlopen(base + "/", timeout=5) as resp:
            assert resp.status == 200
            assert resp.headers["Content-Type"] == "text/html; charset=utf-8"
            page = resp.read().decode("utf-8")
        assert page.startswith("<!DOCTYPE html>")
        assert "Rubric scorecard" in page
        assert "nginx" not in page  # same no-content rule as the terminal dash
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(base + "/favicon.ico", timeout=5)
        assert caught.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_dash_smoke(env):
    result = runner.invoke(app, ["dash", "--plain"])
    assert result.exit_code == 0
    assert "nginx" not in result.output  # no prompt content on screen


def test_dash_quiet_week_is_not_an_error(env):
    result = runner.invoke(app, ["dash", "--plain", "--since", "2030-01-01"])
    assert result.exit_code == 0
    assert "quiet week" in result.output


def test_dash_html_writes_standalone_page(env, tmp_path):
    out = tmp_path / "dash.html"
    result = runner.invoke(app, ["dash", "--html", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    page = out.read_text(encoding="utf-8")
    # Self-contained page: real doctype, our panels, no external asset refs, and
    # the format() placeholders/timestamp token are all resolved.
    assert page.startswith("<!DOCTYPE html>")
    assert "Rubric scorecard" in page
    assert "src=" not in page and "http://" not in page and "https://" not in page
    assert "{code}" not in page and "%%GENERATED%%" not in page
    assert "generated " in page
    assert "nginx" not in page  # same no-content rule as the terminal dash
    # HTML mode writes only the file; the dashboard is not dumped to stdout.
    assert "Rubric scorecard" not in result.output


def test_dash_html_quiet_week_still_writes_page(env, tmp_path):
    out = tmp_path / "empty.html"
    result = runner.invoke(app, ["dash", "--html", str(out), "--since", "2030-01-01"])
    assert result.exit_code == 0
    assert out.exists()
    assert "quiet week" in out.read_text(encoding="utf-8")


def test_stats_quiet_week_is_not_an_error(env):
    result = runner.invoke(app, ["stats", "--since", "2030-01-01"])
    assert result.exit_code == 0
    assert "quiet week" in result.output


def test_stats_shows_no_llm_tag(env):
    result = runner.invoke(app, ["stats"])
    assert result.exit_code == 0
    assert "no LLM" in result.output


def test_default_stores_filters_by_enabled(env, monkeypatch):
    from prompt_coach.cli import default_stores
    from prompt_coach.config import load_config

    monkeypatch.setenv("PROMPT_COACH_ENABLED_STORES", "hermes,codex")
    stores = default_stores(load_config())
    assert {s.kind.value for s in stores} == {"hermes", "codex"}


def test_setup_wizard_writes_config_and_respects_answers(env):
    # hermes/claude-code have fixture data (default: enable); copilot/codex
    # point at nonexistent dirs (default: disable). Accept every other
    # default, decline the dir-override and both post-setup actions.
    answers = "\n" * 8 + "n\nn\nn\n"
    result = runner.invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output

    config_path = env / "xdg-config" / "prompt-coach" / "config.toml"
    assert config_path.is_file()
    written = config_path.read_text()
    assert 'enabled = ["claude-code", "hermes"]' in written
    assert "not reachable" in result.output  # PROMPT_COACH_API_BASE is unreachable in tests


def test_setup_wizard_can_disable_a_normally_available_store(env):
    # Decline claude-code specifically (its default is "enable"); accept
    # every other default.
    answers = "\nn\n\n\n\n\n\n\nn\nn\nn\n"
    result = runner.invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output

    config_path = env / "xdg-config" / "prompt-coach" / "config.toml"
    written = config_path.read_text()
    assert 'enabled = ["hermes"]' in written


def test_setup_wizard_rejects_public_url_without_crashing(env):
    # Position 5 (base URL) gets a public URL instead of accepting the
    # default -- RemoteEndpointRefused must be caught, not propagate.
    answers = "\n\n\n\nhttps://api.openai.com/v1\n\n\n\nn\nn\nn\n"
    result = runner.invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output
    assert "refused" in result.output
    assert "not reachable" in result.output

    config_path = env / "xdg-config" / "prompt-coach" / "config.toml"
    written = config_path.read_text()
    assert 'api_base = "https://api.openai.com/v1"' in written  # saved as typed, even if rejected


def test_setup_wizard_dir_override_written(env):
    answers = "\n" * 8 + "y\noff\nn\nn\n"
    result = runner.invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output

    config_path = env / "xdg-config" / "prompt-coach" / "config.toml"
    written = config_path.read_text()
    assert "[nudge.dir_overrides]" in written
    assert '"off"' in written


def test_setup_wizard_notes_when_repo_root_cant_be_found(env):
    # Nothing installed, and the env fixture defaults find_repo_root() to
    # None (a clean machine) -- the offer step should say so and NOT
    # consume a confirm prompt (the existing 8-blanks + 3 answers below
    # already proves that: a stray extra prompt would desync the answers
    # and fail the config-write assertions the same way this test's sibling
    # above does).
    answers = "\n" * 8 + "n\nn\nn\n"
    result = runner.invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output
    assert "Claude Code plugin:" in result.output
    assert "Couldn't auto-detect the repo location" in result.output


def test_setup_wizard_silent_when_plugin_already_set_up(env):
    claude_home = env / "claude_home"
    claude_home.mkdir(parents=True)
    (claude_home / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"prompt-coach@prompt-coach": True}})
    )
    answers = "\n" * 8 + "n\nn\nn\n"
    result = runner.invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output
    assert "Claude Code plugin:" not in result.output


def test_setup_wizard_offers_and_installs_the_plugin(env, monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    (repo / "plugin" / ".claude-plugin").mkdir(parents=True)
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"plugins": [{"name": "prompt-coach", "source": "./plugin"}]})
    )
    (repo / "plugin" / ".claude-plugin" / "plugin.json").write_text("{}")
    monkeypatch.setattr("prompt_coach.plugin.find_repo_root", lambda: repo)
    calls = []
    monkeypatch.setattr("prompt_coach.plugin.install_plugin", lambda root, **k: calls.append(root))

    answers = "\n" * 8 + "n\ny\nn\nn\n"  # ...dir-override(n), plugin-offer(y), report(n), dash(n)
    result = runner.invoke(app, ["setup"], input=answers)
    assert result.exit_code == 0, result.output
    assert calls == [repo]
    assert "Installed the plugin" in result.output


def test_setup_without_a_terminal_exits_cleanly(env, monkeypatch):
    # Without a TTY the first typer.confirm hits EOF and click raises Abort
    # -- exactly what happens when setup runs via the /prompt-coach slash
    # command (seen live 2026-07-31). CliRunner can't reproduce that (its
    # exhausted input stream feeds prompts their defaults instead), so
    # inject the Abort at the wizard boundary. Must exit 0 with a pointer,
    # not "Aborted." + exit 1.
    from click.exceptions import Abort

    def raise_abort():
        raise Abort()

    monkeypatch.setattr("prompt_coach.cli._setup_wizard", raise_abort)
    result = runner.invoke(app, ["setup"])
    assert result.exit_code == 0, result.output
    assert "needs a real terminal" in result.output
    assert not (env / "xdg-config" / "prompt-coach" / "config.toml").exists()


def test_dash_no_sync_skips_sync(env, monkeypatch):
    calls = []
    monkeypatch.setattr("prompt_coach.cache.CacheDB.sync", lambda self, *a, **k: calls.append(1))

    result = runner.invoke(app, ["dash", "--plain", "--no-sync"])
    assert result.exit_code == 0  # nothing in cache yet, never synced -- not an error (D7)
    assert "no cache yet" in result.output
    assert calls == []

    runner.invoke(app, ["dash", "--plain"])
    assert calls == [1]


# ---------------------------------------------------------------------------
# doctor / migrate-legacy: typer.CliRunner level, real filesystem writes
# under env's isolated PROMPT_COACH_CLAUDE_HOME, `claude` always mocked via
# prompt_coach.plugin.install_plugin (see tests/test_plugin_migrate.py for
# the unit-level coverage of that function itself).
# ---------------------------------------------------------------------------


def _write_legacy_setup(claude_home, *, plugin_enabled=False):
    """The exact hand-authored state this plugin replaces (README.md /
    DECISIONS.md 2026-08-07/2026-09-20): two shell-form nudge hooks and
    three slash command files. plugin_enabled=True additionally marks the
    plugin as already installed, for tests of the removal-only path that
    don't want migrate-legacy's repo-root resolution in play at all (dev's
    own uv-tool-installed prompt-coach makes that resolution depend on
    real, uncontrolled machine state -- see DECISIONS.md 2026-09-24)."""
    claude_home.mkdir(parents=True, exist_ok=True)
    (claude_home / "settings.json").write_text(
        json.dumps(
            {
                "enabledPlugins": {"prompt-coach@prompt-coach": True} if plugin_enabled else {},
                "hooks": {
                    "UserPromptSubmit": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/home/x/.local/bin/prompt-coach nudge",
                                    "timeout": 25,
                                }
                            ]
                        }
                    ],
                    "Stop": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "/home/x/.local/bin/prompt-coach nudge",
                                    "timeout": 10,
                                }
                            ]
                        }
                    ],
                },
                "model": "opus",
            }
        )
    )
    commands = claude_home / "commands"
    commands.mkdir(parents=True, exist_ok=True)
    for name in ("prompt-coach.md", "coach-accept.md", "coach-original.md"):
        (commands / name).write_text(f"# {name}\n")


def test_doctor_on_a_clean_machine(env):
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "plugin enabled:  no" in result.output
    assert "legacy hooks:    0 found" in result.output
    assert "Plugin not installed" in result.output


def test_doctor_json_reports_legacy_state(env):
    _write_legacy_setup(env / "claude_home")
    result = runner.invoke(app, ["doctor", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data["legacy_hooks"]) == 2
    assert len(data["legacy_commands"]) == 3
    assert data["plugin_enabled"] is False


def test_doctor_flags_double_fire_risk(env):
    claude_home = env / "claude_home"
    _write_legacy_setup(claude_home, plugin_enabled=True)

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "nudged twice" in result.output


def test_migrate_legacy_nothing_to_do(env):
    claude_home = env / "claude_home"
    claude_home.mkdir(parents=True)
    (claude_home / "settings.json").write_text(
        json.dumps({"enabledPlugins": {"prompt-coach@prompt-coach": True}})
    )
    result = runner.invoke(app, ["migrate-legacy"])
    assert result.exit_code == 0
    assert "Nothing to do" in result.output


def test_migrate_legacy_dry_run_changes_nothing(env):
    claude_home = env / "claude_home"
    # plugin_enabled=True: this test is about the confirmation/dry-run
    # mechanics, not repo-root resolution for an install.
    _write_legacy_setup(claude_home, plugin_enabled=True)
    original = (claude_home / "settings.json").read_text()

    result = runner.invoke(app, ["migrate-legacy", "--dry-run"])
    assert result.exit_code == 0
    assert "remove the legacy UserPromptSubmit hook" in result.output
    assert (claude_home / "settings.json").read_text() == original
    assert (claude_home / "commands" / "coach-accept.md").exists()


def test_migrate_legacy_declined_changes_nothing(env):
    claude_home = env / "claude_home"
    _write_legacy_setup(claude_home, plugin_enabled=True)
    original = (claude_home / "settings.json").read_text()

    result = runner.invoke(app, ["migrate-legacy"], input="n\n")
    assert result.exit_code == 0
    assert "Cancelled" in result.output
    assert (claude_home / "settings.json").read_text() == original


def test_migrate_legacy_without_a_terminal_exits_cleanly(env, monkeypatch):
    # As with setup (see test_setup_without_a_terminal_exits_cleanly):
    # CliRunner's exhausted input stream feeds typer.confirm its default
    # instead of raising Abort, so inject the Abort directly to reproduce
    # what a real EOF (e.g. via the /prompt-coach slash command) does.
    from click.exceptions import Abort

    claude_home = env / "claude_home"
    _write_legacy_setup(claude_home, plugin_enabled=True)

    def raise_abort(*a, **k):
        raise Abort()

    monkeypatch.setattr("typer.confirm", raise_abort)
    result = runner.invoke(app, ["migrate-legacy"])
    assert result.exit_code == 0
    assert "needs a real terminal" in result.output


def test_migrate_legacy_removes_hooks_and_commands_only(env, monkeypatch):
    # Plugin already enabled -- only the removal half runs, no claude subprocess.
    claude_home = env / "claude_home"
    _write_legacy_setup(claude_home, plugin_enabled=True)

    def boom(*a, **k):
        raise AssertionError("install_plugin should not run when already enabled")

    monkeypatch.setattr("prompt_coach.plugin.install_plugin", boom)

    result = runner.invoke(app, ["migrate-legacy", "--yes"])
    assert result.exit_code == 0, result.output
    after = json.loads((claude_home / "settings.json").read_text())
    assert "hooks" not in after
    assert after["model"] == "opus"  # unrelated key survives
    assert not (claude_home / "commands" / "coach-accept.md").exists()
    assert any(
        p.name.startswith("coach-accept.md.bak-") for p in (claude_home / "commands").iterdir()
    )


def test_migrate_legacy_installs_plugin_when_missing(env, monkeypatch, tmp_path):
    claude_home = env / "claude_home"
    _write_legacy_setup(claude_home)
    repo = tmp_path / "repo"
    (repo / "plugin" / ".claude-plugin").mkdir(parents=True)
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"plugins": [{"name": "prompt-coach", "source": "./plugin"}]})
    )
    (repo / "plugin" / ".claude-plugin" / "plugin.json").write_text("{}")

    calls = []

    def fake_install(root, **k):
        # The real `claude plugin install` writes its own keys into this
        # same settings.json -- reproduce that side effect so this test
        # would have caught the stale-plan-clobbers-the-install-write bug
        # found live 2026-09-24 (see _execute_migration's docstring).
        calls.append(root)
        settings_path = claude_home / "settings.json"
        settings = json.loads(settings_path.read_text())
        settings["enabledPlugins"] = {"prompt-coach@prompt-coach": True}
        settings_path.write_text(json.dumps(settings))

    # --repo is passed explicitly below, so find_repo_root() is never
    # consulted here -- only install_plugin needs mocking.
    monkeypatch.setattr("prompt_coach.plugin.install_plugin", fake_install)

    result = runner.invoke(app, ["migrate-legacy", "--repo", str(repo), "--yes"])
    assert result.exit_code == 0, result.output
    assert calls == [repo]
    assert "Installed the plugin" in result.output
    after = json.loads((claude_home / "settings.json").read_text())
    assert "hooks" not in after
    assert after["enabledPlugins"] == {
        "prompt-coach@prompt-coach": True
    }  # install's write survives


def test_migrate_legacy_install_failure_leaves_hooks_untouched(env, monkeypatch, tmp_path):
    claude_home = env / "claude_home"
    _write_legacy_setup(claude_home)
    repo = tmp_path / "repo"
    (repo / "plugin" / ".claude-plugin").mkdir(parents=True)
    (repo / ".claude-plugin").mkdir()
    (repo / ".claude-plugin" / "marketplace.json").write_text(
        json.dumps({"plugins": [{"name": "prompt-coach", "source": "./plugin"}]})
    )
    (repo / "plugin" / ".claude-plugin" / "plugin.json").write_text("{}")
    original = (claude_home / "settings.json").read_text()

    from prompt_coach.plugin import ClaudeCLIError

    def fail(*a, **k):
        raise ClaudeCLIError(["claude"], 1, "boom")

    monkeypatch.setattr("prompt_coach.plugin.install_plugin", fail)

    result = runner.invoke(app, ["migrate-legacy", "--repo", str(repo), "--yes"])
    assert result.exit_code == 1
    assert "Plugin install failed, nothing else touched" in result.output
    assert (claude_home / "settings.json").read_text() == original
    assert (claude_home / "commands" / "coach-accept.md").exists()


def test_migrate_legacy_unknown_repo_location(env):
    # Plugin not enabled, no --repo given, and env already makes
    # find_repo_root() report "not found" (see the env fixture).
    claude_home = env / "claude_home"
    claude_home.mkdir(parents=True)

    result = runner.invoke(app, ["migrate-legacy"])
    assert result.exit_code == 1
    assert "--repo" in result.output


def test_migrate_legacy_rejects_a_path_that_is_not_the_repo(env, tmp_path):
    claude_home = env / "claude_home"
    claude_home.mkdir(parents=True)
    (claude_home / "settings.json").write_text(json.dumps({"enabledPlugins": {}}))
    result = runner.invoke(app, ["migrate-legacy", "--repo", str(tmp_path)])
    assert result.exit_code == 1
    assert "doesn't look like the prompt-coach repo" in result.output


def test_setup_wizard_preserves_settings_it_does_not_ask_about(env):
    # The wizard only asks about some keys; rebuilding the dataclasses by
    # hand used to drop the rest on rewrite (api_key_cmd, reasoning,
    # auto_rewrite added 2026-09-27).
    config_path = env / "xdg-config" / "prompt-coach" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '[llm]\napi_key_cmd = "echo k"\nreasoning = false\n\n[nudge]\nauto_rewrite = true\n'
    )
    result = runner.invoke(app, ["setup"], input="\n" * 8 + "n\nn\nn\n")
    assert result.exit_code == 0, result.output
    written = config_path.read_text()
    assert 'api_key_cmd = "echo k"' in written
    assert "reasoning = false" in written
    assert "auto_rewrite = true" in written
