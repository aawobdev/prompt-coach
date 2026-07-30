"""Import-hygiene regression guard.

2026-07-29 found that `cli.py`/`nudge.py` eagerly importing the `openai`
SDK (via llm.client) cost ~700-900ms on every single Claude Code prompt
submission, regardless of nudge mode -- see DECISIONS.md. Fixed by moving
those imports local to the functions that actually call the LLM. This
guards the fix: a careless top-level `from prompt_coach.llm.client import
...` added back to either module would silently reintroduce the latency.

Must run each check in a fresh subprocess: importing within the same
pytest process risks other test modules (test_llm_client.py, etc.) having
already pulled `openai` into `sys.modules`, which would make the check
meaningless regardless of import order.
"""

from __future__ import annotations

import subprocess
import sys


def _openai_imported_by(module: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-c", f"import sys, {module}; print('openai' in sys.modules)"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip() == "True"


def test_importing_nudge_does_not_pull_in_openai():
    assert not _openai_imported_by("prompt_coach.nudge")


def test_importing_cli_does_not_pull_in_openai():
    assert not _openai_imported_by("prompt_coach.cli")
