"""Guards for the Claude Code plugin under plugin/ (marketplace at the repo root).

The plugin is a thin wrapper: hooks and skills that call the installed
prompt-coach CLI through a fail-open launcher (plugin/scripts/pc). These
tests keep the pieces consistent with each other and with the CLI, since
nothing else would notice a renamed subcommand or a lost exec bit until a
live Claude Code session silently stopped nudging.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess
import tomllib
from pathlib import Path

import pytest
import typer.main

from prompt_coach.cli import app
from prompt_coach.nudge import ACCEPT_COMMAND, ORIGINAL_COMMAND

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugin"
LAUNCHER = PLUGIN / "scripts" / "pc"
PLUGIN_NAME = "prompt-coach"


def _json(path: Path) -> dict:
    return json.loads(path.read_text())


def _frontmatter(path: Path) -> dict[str, str]:
    lines = path.read_text().splitlines()
    assert lines[0] == "---", f"{path} has no frontmatter"
    end = lines.index("---", 1)
    fields = {}
    for line in lines[1:end]:
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


class TestManifests:
    def test_plugin_version_matches_pyproject(self):
        manifest = _json(PLUGIN / ".claude-plugin" / "plugin.json")
        pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
        assert manifest["version"] == pyproject["project"]["version"]

    def test_plugin_name_is_the_namespace_the_nudge_text_uses(self):
        manifest = _json(PLUGIN / ".claude-plugin" / "plugin.json")
        assert manifest["name"] == PLUGIN_NAME
        assert ACCEPT_COMMAND == f"/{PLUGIN_NAME}:accept"
        assert ORIGINAL_COMMAND == f"/{PLUGIN_NAME}:original"

    def test_marketplace_points_at_the_plugin_dir(self):
        market = _json(ROOT / ".claude-plugin" / "marketplace.json")
        entries = {p["name"]: p for p in market["plugins"]}
        assert PLUGIN_NAME in entries
        source = entries[PLUGIN_NAME]["source"]
        assert source.startswith("./")
        assert (ROOT / source / ".claude-plugin" / "plugin.json").is_file()


class TestHooks:
    def _hooks(self) -> dict:
        data = _json(PLUGIN / "hooks" / "hooks.json")
        # Plugin hooks.json needs the top-level "hooks" key (settings.json
        # style without it is silently ignored).
        assert "hooks" in data
        return data["hooks"]

    def test_registers_session_start_user_prompt_submit_and_stop(self):
        assert set(self._hooks()) == {"SessionStart", "UserPromptSubmit", "Stop"}

    def test_session_start_fires_on_new_sessions_only(self):
        # Not resume/clear/compact/fork: the summary is a once-per-session
        # greeting, not something to repeat mid-conversation.
        assert [g["matcher"] for g in self._hooks()["SessionStart"]] == ["startup"]

    def test_every_hook_is_exec_form_through_the_launcher(self):
        for event, groups in self._hooks().items():
            for group in groups:
                for hook in group["hooks"]:
                    assert hook["type"] == "command", event
                    assert hook["command"] == "${CLAUDE_PLUGIN_ROOT}/scripts/pc", event
                    assert hook["args"] == ["nudge"], event

    def test_timeouts_match_the_settings_json_they_replace(self):
        hooks = self._hooks()
        assert hooks["UserPromptSubmit"][0]["hooks"][0]["timeout"] == 25
        assert hooks["Stop"][0]["hooks"][0]["timeout"] == 10
        # SessionStart runs at launch and a prompt sent meanwhile waits for
        # it, so keep it tight; it only reads a JSON file and one query.
        assert hooks["SessionStart"][0]["hooks"][0]["timeout"] == 5

    def test_launcher_is_executable(self):
        assert LAUNCHER.is_file()
        assert LAUNCHER.stat().st_mode & stat.S_IXUSR


class TestSkills:
    SKILLS = ("accept", "original", "run")

    def test_skill_dirs_are_exactly_the_expected_set(self):
        found = {p.name for p in (PLUGIN / "skills").iterdir() if p.is_dir()}
        assert found == set(self.SKILLS)

    @pytest.mark.parametrize("skill", SKILLS)
    def test_skill_is_user_only_and_pre_approves_only_the_launcher(self, skill):
        fm = _frontmatter(PLUGIN / "skills" / skill / "SKILL.md")
        assert fm["description"]
        # These resend or run commands on the user's behalf: Claude must not
        # be able to trigger them itself.
        assert fm["disable-model-invocation"] == "true"
        assert fm["allowed-tools"].startswith("Bash(${CLAUDE_PLUGIN_ROOT}/scripts/pc")

    @pytest.mark.parametrize("skill", ["accept", "original"])
    def test_resend_skills_inject_a_real_cli_subcommand(self, skill):
        body = (PLUGIN / "skills" / skill / "SKILL.md").read_text()
        want = "rewritten" if skill == "accept" else "original"
        # The injection line itself, not the allowed-tools rule that also
        # mentions the subcommand.
        expected = (
            "!`${CLAUDE_PLUGIN_ROOT}/scripts/pc nudge-consume "
            f'--want {want} --session "${{CLAUDE_SESSION_ID}}"`'
        )
        assert expected in body.splitlines()
        assert "nudge-consume" in typer.main.get_command(app).commands

    def test_run_skill_passes_arguments_through(self):
        body = (PLUGIN / "skills" / "run" / "SKILL.md").read_text()
        assert "!`${CLAUDE_PLUGIN_ROOT}/scripts/pc $ARGUMENTS`" in body.splitlines()


def _run_launcher(args, stdin="", **env_overrides):
    env = {"PATH": "/nonexistent", "HOME": "/nonexistent"}
    env.update(env_overrides)
    return subprocess.run(
        ["/bin/sh", str(LAUNCHER), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )


class TestLauncher:
    def _fake_cli(self, directory: Path) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        cli = directory / "prompt-coach"
        # Builtins only: the thin-PATH test deliberately has no `cat`.
        cli.write_text('#!/bin/sh\nIFS= read -r line\necho "ARGS:$* STDIN:$line"\n')
        cli.chmod(0o755)
        return cli

    def test_execs_cli_from_path_and_passes_args_and_stdin(self, tmp_path):
        self._fake_cli(tmp_path / "bin")
        result = _run_launcher(
            ["nudge"],
            stdin="PAYLOAD",
            PATH=f"{tmp_path / 'bin'}:/usr/bin:/bin",
            TMPDIR=str(tmp_path),
        )
        assert result.returncode == 0
        assert result.stdout == "ARGS:nudge STDIN:PAYLOAD\n"

    def test_falls_back_to_home_local_bin_when_path_is_thin(self, tmp_path):
        # The desktop app / IDE case: prompt-coach isn't on PATH but uv put
        # it in ~/.local/bin.
        self._fake_cli(tmp_path / ".local" / "bin")
        result = _run_launcher(["nudge"], stdin="X", HOME=str(tmp_path), TMPDIR=str(tmp_path))
        assert result.returncode == 0
        assert result.stdout == "ARGS:nudge STDIN:X\n"

    def test_hook_call_fails_open_and_warns_only_once(self, tmp_path):
        first = _run_launcher(["nudge"], TMPDIR=str(tmp_path))
        second = _run_launcher(["nudge"], TMPDIR=str(tmp_path))
        assert first.returncode == 0 and second.returncode == 0
        assert "systemMessage" in json.loads(first.stdout)
        assert json.loads(second.stdout) == {}

    def test_non_hook_call_prints_install_hint_and_exits_zero(self, tmp_path):
        result = _run_launcher(["report"], TMPDIR=str(tmp_path))
        assert result.returncode == 0
        assert "install.sh" in result.stdout

    def test_needs_no_external_commands(self, tmp_path):
        # PATH points nowhere: the launcher must rely on shell builtins only.
        result = _run_launcher(["nudge"], TMPDIR=str(tmp_path))
        assert result.returncode == 0
        assert result.stderr == ""


def test_launcher_is_posix_sh_not_bash():
    assert LAUNCHER.read_text().startswith("#!/bin/sh\n")
    assert os.access(LAUNCHER, os.X_OK)
