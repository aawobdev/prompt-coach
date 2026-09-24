"""Unit tests for prompt_coach.plugin: repo-root discovery, legacy hook/
command detection and removal planning, and the claude-CLI install wrapper.

No real `claude` binary is invoked here -- see TestDoctor/TestMigrateLegacy
in tests/test_cli.py for the typer.CliRunner-level tests that go through
doctor/migrate-legacy with subprocess mocked the same way.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from prompt_coach.plugin import (
    ClaudeCLIError,
    HookRemoval,
    apply_hook_removal,
    backup_and_remove_legacy_commands,
    find_legacy_command_files,
    find_repo_root,
    install_plugin,
    is_plugin_enabled,
    is_valid_plugin_repo,
    plan_hook_removal,
    plan_migration,
)

# The plugin's own hook, exactly as plugin/hooks/hooks.json declares it
# (tests/test_plugin.py locks the source of truth down; this is the shape
# `claude plugin install` writes into settings.json's merged view -- but in
# practice the plugin's hooks live in the plugin manifest, not settings.json,
# so this fixture exists to prove removal logic leaves an exec-form entry
# alone even if one turned up there).
PLUGIN_HOOK = {
    "type": "command",
    "command": "${CLAUDE_PLUGIN_ROOT}/scripts/pc",
    "args": ["nudge"],
    "timeout": 25,
}
LEGACY_HOOK = {
    "type": "command",
    "command": "/home/alistair/.local/bin/prompt-coach nudge",
    "timeout": 25,
}


class TestFindRepoRoot:
    def test_reads_directory_from_uv_receipt(self, tmp_path, monkeypatch):
        tools_dir = tmp_path / "tools"
        receipt_dir = tools_dir / "prompt-coach"
        receipt_dir.mkdir(parents=True)
        repo = tmp_path / "repo"
        repo.mkdir()
        (receipt_dir / "uv-receipt.toml").write_text(
            f'[tool]\nrequirements = [{{ name = "prompt-coach", directory = "{repo}" }}]\n'
        )

        def fake_run(args, **kwargs):
            assert args[-2:] == ["tool", "dir"]
            return subprocess.CompletedProcess(args, 0, stdout=str(tools_dir) + "\n", stderr="")

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv")
        monkeypatch.setattr("subprocess.run", fake_run)
        assert find_repo_root() == repo

    def test_none_when_uv_missing(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: None)
        assert find_repo_root() is None

    def test_none_when_receipt_missing(self, tmp_path, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv")
        monkeypatch.setattr(
            "subprocess.run",
            lambda args, **kw: subprocess.CompletedProcess(
                args, 0, stdout=str(tmp_path), stderr=""
            ),
        )
        assert find_repo_root() is None

    def test_none_when_receipt_malformed(self, tmp_path, monkeypatch):
        receipt_dir = tmp_path / "prompt-coach"
        receipt_dir.mkdir()
        (receipt_dir / "uv-receipt.toml").write_text("not valid toml [[[")
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv")
        monkeypatch.setattr(
            "subprocess.run",
            lambda args, **kw: subprocess.CompletedProcess(
                args, 0, stdout=str(tmp_path), stderr=""
            ),
        )
        assert find_repo_root() is None

    def test_none_when_recorded_directory_no_longer_exists(self, tmp_path, monkeypatch):
        receipt_dir = tmp_path / "prompt-coach"
        receipt_dir.mkdir()
        gone = tmp_path / "gone"
        (receipt_dir / "uv-receipt.toml").write_text(
            f'[tool]\nrequirements = [{{ name = "prompt-coach", directory = "{gone}" }}]\n'
        )
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv")
        monkeypatch.setattr(
            "subprocess.run",
            lambda args, **kw: subprocess.CompletedProcess(
                args, 0, stdout=str(tmp_path), stderr=""
            ),
        )
        assert find_repo_root() is None

    def test_none_when_uv_tool_dir_fails(self, monkeypatch):
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/uv")

        def fake_run(args, **kwargs):
            raise subprocess.CalledProcessError(1, args)

        monkeypatch.setattr("subprocess.run", fake_run)
        assert find_repo_root() is None


class TestIsValidPluginRepo:
    def _repo(self, tmp_path, plugin_dir="plugin", plugin_name="prompt-coach"):
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"plugins": [{"name": plugin_name, "source": f"./{plugin_dir}"}]})
        )
        return tmp_path

    def test_valid_repo(self, tmp_path):
        repo = self._repo(tmp_path)
        (tmp_path / "plugin" / ".claude-plugin").mkdir(parents=True)
        (tmp_path / "plugin" / ".claude-plugin" / "plugin.json").write_text("{}")
        assert is_valid_plugin_repo(repo) is True

    def test_missing_marketplace_manifest(self, tmp_path):
        assert is_valid_plugin_repo(tmp_path) is False

    def test_marketplace_manifest_missing_our_plugin(self, tmp_path):
        repo = self._repo(tmp_path, plugin_name="something-else")
        assert is_valid_plugin_repo(repo) is False

    def test_source_dir_has_no_plugin_manifest(self, tmp_path):
        repo = self._repo(tmp_path)
        assert is_valid_plugin_repo(repo) is False  # plugin/.claude-plugin/plugin.json never made

    def test_malformed_marketplace_json(self, tmp_path):
        (tmp_path / ".claude-plugin").mkdir()
        (tmp_path / ".claude-plugin" / "marketplace.json").write_text("not json")
        assert is_valid_plugin_repo(tmp_path) is False


class TestPlanHookRemoval:
    def test_finds_legacy_hooks_on_both_events(self):
        settings = {
            "hooks": {
                "UserPromptSubmit": [{"hooks": [LEGACY_HOOK]}],
                "Stop": [{"hooks": [{**LEGACY_HOOK, "timeout": 10}]}],
            }
        }
        removals, after = plan_hook_removal(settings)
        assert removals == [
            HookRemoval("UserPromptSubmit", LEGACY_HOOK["command"]),
            HookRemoval("Stop", LEGACY_HOOK["command"]),
        ]
        assert "hooks" not in after

    def test_does_not_mutate_the_input(self):
        settings = {"hooks": {"UserPromptSubmit": [{"hooks": [LEGACY_HOOK]}]}}
        plan_hook_removal(settings)
        assert settings["hooks"]["UserPromptSubmit"] == [{"hooks": [LEGACY_HOOK]}]

    def test_leaves_unrelated_hooks_in_the_same_group_alone(self):
        other_hook = {"type": "command", "command": "echo hi"}
        settings = {"hooks": {"UserPromptSubmit": [{"hooks": [LEGACY_HOOK, other_hook]}]}}
        removals, after = plan_hook_removal(settings)
        assert len(removals) == 1
        assert after["hooks"]["UserPromptSubmit"] == [{"hooks": [other_hook]}]

    def test_leaves_a_different_event_entirely_alone(self):
        settings = {
            "hooks": {
                "UserPromptSubmit": [{"hooks": [LEGACY_HOOK]}],
                "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}],
            }
        }
        _, after = plan_hook_removal(settings)
        assert after["hooks"]["PreToolUse"] == settings["hooks"]["PreToolUse"]
        assert "UserPromptSubmit" not in after["hooks"]

    def test_never_matches_the_plugins_own_exec_form_hook(self):
        settings = {"hooks": {"UserPromptSubmit": [{"hooks": [PLUGIN_HOOK]}]}}
        removals, after = plan_hook_removal(settings)
        assert removals == []
        assert after == settings

    def test_legacy_and_plugin_hook_side_by_side_only_removes_legacy(self):
        # The state right after installing the plugin but before migrating:
        # both fire. Only the legacy one should ever be flagged.
        settings = {"hooks": {"UserPromptSubmit": [{"hooks": [LEGACY_HOOK, PLUGIN_HOOK]}]}}
        removals, after = plan_hook_removal(settings)
        assert len(removals) == 1
        assert after["hooks"]["UserPromptSubmit"] == [{"hooks": [PLUGIN_HOOK]}]

    def test_no_hooks_key_is_a_no_op(self):
        settings = {"model": "opus"}
        removals, after = plan_hook_removal(settings)
        assert removals == []
        assert after == settings

    def test_hooks_key_present_but_not_a_dict_is_a_no_op(self):
        settings = {"hooks": "not a dict"}
        removals, after = plan_hook_removal(settings)
        assert removals == []
        assert after == settings

    def test_malformed_event_value_is_skipped_not_crashed(self):
        settings = {"hooks": {"UserPromptSubmit": "not a list", "Stop": [{"hooks": [LEGACY_HOOK]}]}}
        removals, after = plan_hook_removal(settings)
        assert len(removals) == 1
        assert after["hooks"]["UserPromptSubmit"] == "not a list"


class TestFindLegacyCommandFiles:
    def test_finds_only_files_that_exist(self, tmp_path):
        (tmp_path / "coach-accept.md").write_text("x")
        (tmp_path / "unrelated.md").write_text("x")
        found = find_legacy_command_files(tmp_path)
        assert found == [tmp_path / "coach-accept.md"]

    def test_missing_dir_is_empty_not_an_error(self, tmp_path):
        assert find_legacy_command_files(tmp_path / "nope") == []


class TestPlanMigration:
    def test_reads_both_hooks_and_commands_from_claude_home(self, tmp_path):
        (tmp_path / "commands").mkdir()
        (tmp_path / "commands" / "coach-accept.md").write_text("x")
        (tmp_path / "settings.json").write_text(
            json.dumps({"hooks": {"UserPromptSubmit": [{"hooks": [LEGACY_HOOK]}]}})
        )
        plan = plan_migration(tmp_path)
        assert plan.needs_migration
        assert plan.has_legacy_hooks
        assert plan.has_legacy_commands
        assert plan.legacy_command_files == [tmp_path / "commands" / "coach-accept.md"]

    def test_nothing_to_migrate_on_a_clean_machine(self, tmp_path):
        plan = plan_migration(tmp_path)
        assert not plan.needs_migration

    def test_malformed_settings_json_is_treated_as_empty(self, tmp_path):
        (tmp_path / "settings.json").write_text("{not json")
        plan = plan_migration(tmp_path)
        assert not plan.has_legacy_hooks


class TestIsPluginEnabled:
    def test_true_when_enabled_key_present_and_true(self, tmp_path):
        (tmp_path / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"prompt-coach@prompt-coach": True}})
        )
        assert is_plugin_enabled(tmp_path) is True

    def test_false_when_absent(self, tmp_path):
        (tmp_path / "settings.json").write_text(json.dumps({"enabledPlugins": {}}))
        assert is_plugin_enabled(tmp_path) is False

    def test_false_when_no_settings_file(self, tmp_path):
        assert is_plugin_enabled(tmp_path) is False

    def test_false_on_malformed_settings(self, tmp_path):
        (tmp_path / "settings.json").write_text("{not json")
        assert is_plugin_enabled(tmp_path) is False


class TestApplyHookRemoval:
    def test_backs_up_and_writes_the_stripped_settings(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        original = {"hooks": {"UserPromptSubmit": [{"hooks": [LEGACY_HOOK]}]}, "model": "opus"}
        settings_path.write_text(json.dumps(original))
        plan = plan_migration(tmp_path)

        backup = apply_hook_removal(settings_path, plan)

        assert backup is not None and backup.is_file()
        assert json.loads(backup.read_text()) == original
        after = json.loads(settings_path.read_text())
        assert "hooks" not in after
        assert after["model"] == "opus"  # unrelated top-level keys survive

    def test_no_op_and_no_backup_when_nothing_to_remove(self, tmp_path):
        settings_path = tmp_path / "settings.json"
        settings_path.write_text(json.dumps({"model": "opus"}))
        plan = plan_migration(tmp_path)

        backup = apply_hook_removal(settings_path, plan)

        assert backup is None
        assert list(tmp_path.iterdir()) == [settings_path]  # nothing else written


class TestBackupAndRemoveLegacyCommands:
    def test_renames_each_file_and_returns_backup_paths(self, tmp_path):
        (tmp_path / "commands").mkdir()
        accept = tmp_path / "commands" / "coach-accept.md"
        accept.write_text("original content")
        plan = plan_migration(tmp_path)

        backups = backup_and_remove_legacy_commands(plan)

        assert not accept.exists()
        assert len(backups) == 1
        assert backups[0].read_text() == "original content"
        assert backups[0].name.startswith("coach-accept.md.bak-")

    def test_empty_plan_removes_nothing(self, tmp_path):
        plan = plan_migration(tmp_path)
        assert backup_and_remove_legacy_commands(plan) == []


class TestInstallPlugin:
    def test_runs_marketplace_add_then_install_in_order(self, tmp_path, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="ok", stderr="")

        monkeypatch.setattr("subprocess.run", fake_run)
        install_plugin(tmp_path, claude_bin="claude")

        assert calls[0] == ["claude", "plugin", "marketplace", "add", str(tmp_path)]
        assert calls[1] == [
            "claude",
            "plugin",
            "install",
            "prompt-coach@prompt-coach",
            "--scope",
            "user",
            "-y",
        ]

    def test_raises_claude_cli_error_on_nonzero_exit(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "subprocess.run",
            lambda args, **kw: subprocess.CompletedProcess(
                args, 1, stdout="", stderr="Path does not exist"
            ),
        )
        with pytest.raises(ClaudeCLIError, match="Path does not exist"):
            install_plugin(tmp_path)

    def test_stops_after_marketplace_add_fails_without_trying_install(self, tmp_path, monkeypatch):
        calls = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(args, 1, stdout="", stderr="boom")

        monkeypatch.setattr("subprocess.run", fake_run)
        with pytest.raises(ClaudeCLIError):
            install_plugin(tmp_path)
        assert len(calls) == 1

    def test_raises_claude_cli_error_when_binary_missing(self, tmp_path, monkeypatch):
        def fake_run(args, **kwargs):
            raise FileNotFoundError("no such file")

        monkeypatch.setattr("subprocess.run", fake_run)
        with pytest.raises(ClaudeCLIError):
            install_plugin(tmp_path)
