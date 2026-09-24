"""Streak state: per-cache isolation."""

from prompt_coach.streaks import get_score_history, record_prompt


def test_streaks_do_not_leak_between_caches(tmp_path):
    """A fresh cache used to get a shallow copy of the module defaults, so
    every fresh cache shared one history list and record_prompt appended
    into it (found 2026-09-24)."""
    a, b = tmp_path / "a", tmp_path / "b"
    record_prompt(a, "one prompt", was_nudged=False)
    record_prompt(a, "two prompt", was_nudged=False)
    assert get_score_history(b) == []
    assert len(get_score_history(a)) == 2
