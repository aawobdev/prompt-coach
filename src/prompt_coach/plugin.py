"""Claude Code plugin install/migration helpers.

Two separate concerns live here, kept apart because only one of them has a
real `claude` CLI command behind it:

- Installing the plugin (registering this repo as a marketplace, enabling
  the plugin) is done by shelling out to the real `claude` CLI -- it owns
  settings.json's plugin bookkeeping (`extraKnownMarketplaces`,
  `enabledPlugins`), so this module never hand-edits that part of the file.
- Removing the legacy hand-authored hooks and slash command files this
  plugin replaces has no `claude` command to do it (there is no "remove
  this one hook" command), so that part IS hand-edited JSON, done as
  narrowly as possible and always behind a plan you can inspect before
  anything is written (see plan_migration/apply_*).

Everything here is a pure function over paths/dicts except the two `claude`
subprocess calls and the file writes in apply_hook_removal/
backup_and_remove_legacy_commands; cli.py's `doctor` and `migrate-legacy`
commands own confirmation and user-facing output.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
import tomllib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

PLUGIN_NAME = "prompt-coach"
LEGACY_COMMAND_FILES = ("prompt-coach.md", "coach-accept.md", "coach-original.md")
_LEGACY_HOOK_EVENTS = ("UserPromptSubmit", "Stop")

# The hand-authored shell-form hook this plugin replaces: a bare
# "<path-ending-in-prompt-coach> nudge" command string, no `args` key
# (exec form). The plugin's own hook is exec-form with command
# "${CLAUDE_PLUGIN_ROOT}/scripts/pc" and args ["nudge"] (see
# plugin/hooks/hooks.json), so this pattern never matches it -- verified in
# tests/test_plugin.py and tests/test_plugin_migrate.py.
_LEGACY_NUDGE_COMMAND = re.compile(r"prompt-coach(\.exe)?\s+nudge\s*$")


# ---------------------------------------------------------------------------
# Repo root discovery
# ---------------------------------------------------------------------------


def find_repo_root(tool_name: str = PLUGIN_NAME) -> Path | None:
    """Where uv installed this CLI from, read from uv's own tool receipt --
    the one place that fact is recorded. `uv tool install .` copies the
    package into an isolated venv (confirmed live 2026-09-24: this CLI's
    own __file__ ends up under ~/.local/share/uv/tools/prompt-coach/lib/...,
    nothing under it points back at the git checkout), but `uv tool dir`
    plus `<tool>/uv-receipt.toml`'s `tool.requirements[0].directory` does.
    Returns None if `uv` isn't on PATH, this wasn't installed with
    `uv tool install`, or the receipt is missing/malformed -- callers need
    a manual fallback (an explicit --repo)."""
    uv = shutil.which("uv")
    if uv is None:
        return None
    try:
        result = subprocess.run(
            [uv, "tool", "dir"], capture_output=True, text=True, timeout=10, check=True
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    receipt = Path(result.stdout.strip()) / tool_name / "uv-receipt.toml"
    try:
        data = tomllib.loads(receipt.read_text())
        directory = data["tool"]["requirements"][0]["directory"]
    except (OSError, KeyError, IndexError, TypeError, tomllib.TOMLDecodeError):
        return None
    path = Path(directory)
    return path if path.is_dir() else None


def is_valid_plugin_repo(path: Path, plugin_name: str = PLUGIN_NAME) -> bool:
    """True if `path` is a marketplace root that serves `plugin_name` from a
    source directory that actually has a plugin manifest -- the same two
    files `claude plugin validate` checks (see tests/test_plugin.py)."""
    manifest = path / ".claude-plugin" / "marketplace.json"
    if not manifest.is_file():
        return False
    try:
        data = json.loads(manifest.read_text())
    except (OSError, json.JSONDecodeError):
        return False
    entries = {p.get("name"): p for p in data.get("plugins", []) if isinstance(p, dict)}
    entry = entries.get(plugin_name)
    source = entry.get("source") if entry else None
    if not isinstance(source, str):
        return False
    return (path / source / ".claude-plugin" / "plugin.json").is_file()


# ---------------------------------------------------------------------------
# Legacy hook / command detection and removal planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HookRemoval:
    event: str
    command: str


@dataclass(frozen=True)
class MigrationPlan:
    hook_removals: list[HookRemoval] = field(default_factory=list)
    legacy_command_files: list[Path] = field(default_factory=list)
    settings_after: dict = field(default_factory=dict)

    @property
    def has_legacy_hooks(self) -> bool:
        return bool(self.hook_removals)

    @property
    def has_legacy_commands(self) -> bool:
        return bool(self.legacy_command_files)

    @property
    def needs_migration(self) -> bool:
        return self.has_legacy_hooks or self.has_legacy_commands


def _is_legacy_nudge_hook(hook: object) -> bool:
    return (
        isinstance(hook, dict)
        and hook.get("type") == "command"
        and "args" not in hook
        and isinstance(hook.get("command"), str)
        and bool(_LEGACY_NUDGE_COMMAND.search(hook["command"]))
    )


def plan_hook_removal(settings: dict) -> tuple[list[HookRemoval], dict]:
    """Find every legacy prompt-coach hook entry in `settings["hooks"]` and
    return (removals, settings-with-them-stripped). Deep-copies `settings`;
    never mutates the caller's dict. Only ever touches the exact hook
    entries matching the legacy shell-form command -- any other hook the
    user has added to UserPromptSubmit/Stop, and the plugin's own exec-form
    hook if it's already installed alongside the legacy one, is left
    untouched."""
    after = copy.deepcopy(settings)
    removals: list[HookRemoval] = []
    hooks = after.get("hooks")
    if not isinstance(hooks, dict):
        return removals, after
    for event in _LEGACY_HOOK_EVENTS:
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept_groups = []
        for group in groups:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list):
                kept_groups.append(group)
                continue
            kept_inner = []
            for hook in inner:
                if _is_legacy_nudge_hook(hook):
                    removals.append(HookRemoval(event, hook["command"]))
                else:
                    kept_inner.append(hook)
            if kept_inner:
                kept_groups.append({**group, "hooks": kept_inner})
            # else: this group held nothing but the legacy hook -> drop it
        if kept_groups:
            hooks[event] = kept_groups
        else:
            hooks.pop(event, None)
    if not hooks:
        after.pop("hooks", None)
    return removals, after


def find_legacy_command_files(commands_dir: Path) -> list[Path]:
    return [commands_dir / name for name in LEGACY_COMMAND_FILES if (commands_dir / name).is_file()]


def plan_migration(claude_home: Path) -> MigrationPlan:
    """Read what's actually on disk under `claude_home` (~/.claude) and
    return the full plan: no writes, safe to call as often as you like."""
    settings_path = claude_home / "settings.json"
    settings: dict = {}
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = {}
    removals, after = plan_hook_removal(settings)
    legacy_files = find_legacy_command_files(claude_home / "commands")
    return MigrationPlan(
        hook_removals=removals, legacy_command_files=legacy_files, settings_after=after
    )


def is_plugin_enabled(claude_home: Path, plugin_name: str = PLUGIN_NAME) -> bool:
    """Read-only: does ~/.claude/settings.json say this plugin is enabled?
    (`claude plugin install` writes `enabledPlugins["<name>@<name>"] = true`
    there -- confirmed live 2026-09-24.) Never raises: a missing or
    malformed settings.json just reads as "not enabled"."""
    settings_path = claude_home / "settings.json"
    if not settings_path.is_file():
        return False
    try:
        data = json.loads(settings_path.read_text())
    except json.JSONDecodeError:
        return False
    enabled = data.get("enabledPlugins")
    return isinstance(enabled, dict) and bool(enabled.get(f"{plugin_name}@{plugin_name}"))


def backup_path(path: Path) -> Path:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.name}.bak-{stamp}")


def apply_hook_removal(settings_path: Path, plan: MigrationPlan) -> Path | None:
    """Back up settings.json (same `<name>.bak-<timestamp>` convention
    already used by hand for CLAUDE.md on this machine), then write it with
    the legacy hooks stripped. Returns the backup path, or None -- and
    touches nothing -- if there was nothing to remove."""
    if not plan.has_legacy_hooks:
        return None
    backup = backup_path(settings_path)
    shutil.copy2(settings_path, backup)
    settings_path.write_text(json.dumps(plan.settings_after, indent=2) + "\n")
    return backup


def backup_and_remove_legacy_commands(plan: MigrationPlan) -> list[Path]:
    """Rename each legacy command file to a `.bak-<timestamp>` sibling so
    Claude Code stops loading it as a slash command, without deleting
    anything outright. Returns the backup paths, in the same order as
    plan.legacy_command_files."""
    backups = []
    for path in plan.legacy_command_files:
        backup = backup_path(path)
        path.rename(backup)
        backups.append(backup)
    return backups


# ---------------------------------------------------------------------------
# Plugin install, via the real `claude` CLI
# ---------------------------------------------------------------------------


class ClaudeCLIError(RuntimeError):
    """A `claude plugin ...` subprocess exited non-zero. `stderr`/`stdout`
    carry whatever it printed, which is usually specific enough to show the
    user directly (e.g. "Path does not exist: ...")."""

    def __init__(self, args: list[str], returncode: int, output: str):
        self.args = args
        self.returncode = returncode
        self.output = output
        super().__init__(f"`{' '.join(args)}` exited {returncode}: {output.strip()}")


def install_plugin(
    repo_root: Path,
    *,
    claude_bin: str = "claude",
    plugin_name: str = PLUGIN_NAME,
    timeout: float = 60.0,
) -> None:
    """Register `repo_root` as a marketplace and install the plugin from it.
    Both calls are idempotent -- adding an already-known marketplace or
    installing an already-installed plugin exits 0 with an "already ..."
    message rather than an error (verified live 2026-09-24 against a
    sandboxed ~/.claude, both calls run twice in a row). Raises
    ClaudeCLIError on any real failure (bad path, plugin not found in the
    marketplace, claude itself missing/broken)."""
    for args in (
        [claude_bin, "plugin", "marketplace", "add", str(repo_root)],
        [
            claude_bin,
            "plugin",
            "install",
            f"{plugin_name}@{plugin_name}",
            "--scope",
            "user",
            "-y",
        ],
    ):
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        except OSError as exc:
            raise ClaudeCLIError(args, -1, str(exc)) from exc
        if result.returncode != 0:
            raise ClaudeCLIError(args, result.returncode, result.stdout + result.stderr)
