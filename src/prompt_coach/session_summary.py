"""One-line coaching summary shown when a Claude Code session starts.

Wired as a SessionStart hook (plugin phase 3). The message goes out as a
JSON `systemMessage`, which Claude Code shows to the user and does not add
to the model's context -- plain-text stdout on SessionStart *would* be
added, so this path must only ever print JSON (checked against the hooks
docs, 2026-09-24). It costs no tokens and never blocks.

Reads existing state only (streaks.json and the nudge audit log); nothing
here scores, syncs, or writes. Counts and categories only, never prompt
content (AGENTS.md privacy rule).

The "good prompt" streak (consecutive scores >= 70) is deliberately not
shown: on the real corpus only 6 of 245 recent prompts scored 70+ (best
streak 1), so it would read "0 in a row" nearly every session. The
prompts-since-last-nudge count and the most common nudge trigger this week
are what carry signal.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

WINDOW = timedelta(days=7)

# Nudge audit triggers are stored as the full tip text (see nudge.py), and
# that text has changed over time, so categorise by a stable phrase rather
# than by exact match. Order matters only for readability.
_CATEGORIES = (
    ("output format", "no output format or example", "name the shape you want back"),
    ("open-ended", "open-ended scope", "say what's in (or out of) scope"),
    ("pasted", "pasting without an ask", "say what you want done with it"),
)


def _category(trigger: str) -> tuple[str, str] | None:
    for needle, label, fix in _CATEGORIES:
        if needle in trigger:
            return label, fix
    return None  # e.g. "always_mode": a mode setting, not a weakness


def _recent_triggers(cache_dir: Path, now: datetime) -> list[str]:
    db = cache_dir / "cache.db"
    if not db.is_file():
        return []  # fresh install: don't create a cache just to read it
    import sqlite3

    from prompt_coach.cache import CacheDB

    try:
        cache = CacheDB(db)
        try:
            rows = cache.get_audit_events(since=now - WINDOW, limit=10_000)
        finally:
            cache.close()
    except (sqlite3.Error, OSError):
        return []  # a locked or corrupt cache just means no weakness line
    return [r.get("trigger") or "" for r in rows]


def build_summary(cache_dir: Path, now: datetime | None = None) -> str | None:
    """The message, or None when there's nothing worth saying (no history
    yet). Never raises on missing/corrupt state -- callers treat None as
    "stay silent"."""
    from prompt_coach.streaks import get_score_history, get_streaks

    now = now or datetime.now(tz=UTC)
    history = get_score_history(cache_dir, limit=1000)  # streaks.py keeps at most 1000
    if not history:
        return None

    streak = int(get_streaks(cache_dir).get("no_intervention_streak", 0))
    ever_nudged = any(entry.get("was_nudged") for entry in history)
    if ever_nudged:
        lead = f"{streak} prompt{'s' if streak != 1 else ''} since your last nudge."
    else:
        lead = f"{len(history)} prompts tracked, no nudges yet."

    counts: dict[tuple[str, str], int] = {}
    triggers = _recent_triggers(cache_dir, now)
    for trigger in triggers:
        cat = _category(trigger)
        if cat is not None:
            counts[cat] = counts.get(cat, 0) + 1
    total = sum(counts.values())
    if not total:
        return f"prompt-coach: {lead} No nudges this week."

    (label, fix), n = max(counts.items(), key=lambda item: item[1])
    return (
        f"prompt-coach: {lead} This week's most common gap: {label} "
        f"({n} of {total} nudges) -- {fix}."
    )


def hook_response_session_start(payload: dict, cache_dir: Path, mode: str) -> dict:
    """SessionStart hook JSON. The plugin registers this with matcher
    "startup", but check `source` too so a resume, /clear, compact or fork
    never repeats it even if the matcher is widened later."""
    if mode == "off" or payload.get("source", "startup") != "startup":
        return {}
    message = build_summary(cache_dir)
    return {"systemMessage": message} if message else {}
