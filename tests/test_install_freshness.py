"""`uv tool install --force .` must install the current source, not a
cached build of an older one.

uv caches the wheel it builds from a local directory and by default only
rebuilds when pyproject.toml/setup.py/setup.cfg change. Before
`[tool.uv] cache-keys` was set, a merge that didn't touch pyproject.toml
reinstalled the previous build, silently (seen live 2026-09-24: the new
SessionStart hook returned nothing until a forced rebuild).

This reproduces the real deploy step: install from a git checkout, commit
an edit, reinstall, and check the edit is what got installed.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

pytestmark = pytest.mark.skipif(
    shutil.which("uv") is None or shutil.which("git") is None, reason="needs uv and git"
)


def _run(args, cwd, env=None):
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def test_reinstall_after_a_code_only_commit_installs_the_new_code(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "src", repo / "src", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(ROOT / "pyproject.toml", repo / "pyproject.toml")
    git = ["git", "-c", "user.email=t@example.com", "-c", "user.name=t"]
    _run(["git", "init", "-q"], repo)
    _run([*git, "add", "-A"], repo)
    _run([*git, "commit", "-qm", "base"], repo)

    # Isolated tool install location; the uv cache stays the shared default
    # so dependencies come from it (fast), while this checkout's own wheel is
    # keyed by its unique tmp path and can't collide with a real install.
    monkeypatch.setenv("UV_TOOL_DIR", str(tmp_path / "tools"))
    monkeypatch.setenv("UV_TOOL_BIN_DIR", str(tmp_path / "bin"))

    _run(["uv", "tool", "install", "-q", "--force", str(repo)], repo)

    # A code-only change, committed, with pyproject.toml untouched.
    target = repo / "src" / "prompt_coach" / "session_summary.py"
    target.write_text(target.read_text() + '\nINSTALL_FRESHNESS_MARKER = "second-build"\n')
    _run([*git, "commit", "-qam", "code-only change"], repo)

    _run(["uv", "tool", "install", "-q", "--force", str(repo)], repo)

    installed = list(
        (tmp_path / "tools").glob(
            "prompt-coach/lib/python*/site-packages/prompt_coach/session_summary.py"
        )
    )
    assert len(installed) == 1
    assert "second-build" in installed[0].read_text(), "reinstall reused a stale build"
