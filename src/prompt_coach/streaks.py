"""Streak tracking for prompt quality: consecutive good prompts and
no-intervention-needed streaks. Persisted to a JSON file in the cache dir.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.analysis.score import score_prompt

_STREAKS_PATH = "streaks.json"
_GOOD_SCORE_THRESHOLD = 70

_DEFAULT_STREAKS = {
    "good_prompt_streak": 0,
    "no_intervention_streak": 0,
    "best_streak": 0,
    "history": [],
}


def _fresh_streaks() -> dict:
    # A new dict AND a new history list every time: `dict(_DEFAULT_STREAKS)`
    # was a shallow copy, so every fresh cache shared one module-level
    # history list and record_prompt appended into it -- history leaked
    # between caches within one process (found 2026-09-24 by the
    # SessionStart summary tests; test_streaks_do_not_leak_between_caches).
    return {**_DEFAULT_STREAKS, "history": []}


def _streaks_file(cache_dir: Path) -> Path:
    return cache_dir / _STREAKS_PATH


def _load_streaks(cache_dir: Path) -> dict:
    path = _streaks_file(cache_dir)
    if not path.is_file():
        return _fresh_streaks()
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return _fresh_streaks()
    if not isinstance(data, dict):
        return _fresh_streaks()
    data.setdefault("good_prompt_streak", 0)
    data.setdefault("no_intervention_streak", 0)
    data.setdefault("best_streak", 0)
    data.setdefault("history", [])
    return data


def _save_streaks(cache_dir: Path, data: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    _streaks_file(cache_dir).write_text(json.dumps(data))


def record_prompt(cache_dir: Path, text: str, was_nudged: bool) -> dict:
    """Record a prompt submission and update streak counters.

    Args:
        cache_dir: Path to the cache directory.
        text: The prompt text.
        was_nudged: Whether the prompt triggered a nudge intervention.

    Returns:
        Updated streak data with current streaks.
    """
    data = _load_streaks(cache_dir)
    score = score_prompt(text)

    # Good prompt streak
    if score >= _GOOD_SCORE_THRESHOLD:
        data["good_prompt_streak"] += 1
        if data["good_prompt_streak"] > data.get("best_streak", 0):
            data["best_streak"] = data["good_prompt_streak"]
    else:
        data["good_prompt_streak"] = 0

    # No-intervention streak
    if not was_nudged:
        data["no_intervention_streak"] += 1
    else:
        data["no_intervention_streak"] = 0

    # History (keep last 1000 entries)
    entry = {
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "score": score,
        "was_nudged": was_nudged,
    }
    data["history"].append(entry)
    data["history"] = data["history"][-1000:]

    _save_streaks(cache_dir, data)
    return {k: v for k, v in data.items() if k != "history"}


def get_streaks(cache_dir: Path) -> dict:
    """Get current streak data without recording a prompt."""
    data = _load_streaks(cache_dir)
    return {k: v for k, v in data.items() if k != "history"}


def get_score_history(cache_dir: Path, limit: int = 50) -> list[dict]:
    """Get recent score history entries."""
    data = _load_streaks(cache_dir)
    return data.get("history", [])[-limit:]
