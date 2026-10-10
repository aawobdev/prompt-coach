"""Hermes session store: read-only access to ~/.hermes/state.db.

Schema facts verified against the live DB 2026-07-20 (see BLUEPRINT.md 4.2).
Hermes runs WAL and may write concurrently, so the DB is opened via a
mode=ro URI (not immutable=1) and every failure degrades to an unavailable
StoreInfo instead of raising.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.models import Prompt, PromptOrigin, SourceKind, StoreInfo, UsageRecord
from prompt_coach.stores.base import classify_origin, content_hash

# s.model is session-level, not per-turn -- Hermes has no per-message model
# column, unlike Claude Code/Codex where the model can change mid-session.
_PROMPT_QUERY = """
SELECT m.id, m.session_id, m.content, m.timestamp, s.source, s.cwd, s.git_repo_root, s.model
FROM messages m
JOIN sessions s ON s.id = m.session_id
WHERE m.role = 'user'
  AND m.active = 1
  AND m.compacted = 0
  AND m.content IS NOT NULL
  AND m.timestamp >= :since
ORDER BY m.timestamp
"""

# Hermes stores every turn the agent receives as role='user', and the gateway
# wraps what the person typed in its own metadata. Formats verified against
# the live state.db 2026-09-24 (2,829 user rows). Nobody types in a cron job
# or a delegated subagent: every row from those sessions is machine-written.
_MACHINE_SOURCES = frozenset({"cron", "subagent"})

# Leading wrappers, stripped repeatedly until none match. Each is gateway
# text around (or quoted into) the typed message, never typed itself.
# Replying-to quotes the bot message a Slack reply answers (1,428 rows);
# the thread block replays earlier thread messages (405).
_REPLYING_TO = re.compile(r'\[Replying to: ".*?"\]\s*\n\s*\n', re.S)
_THREAD_CONTEXT = re.compile(
    r"\[Thread context\b.*?^\[End of thread context\][ \t]*\n?", re.S | re.M
)
# Bracketed notices whose body can span lines (an image notice carries a
# vision description), so the end is found by bracket balance, not regex.
# Note: model-switch notice (cli). The user sent: attachment notice, with
# its follow-up "If you need a closer look" hint.
_NOTICE_OPENERS = ("[Note:", "[The user sent ", "[If you need a closer look")
# Multi-user Slack channels prefix each message with the sender. One word,
# then "] " -- "[Content of x.txt]:" (inlined document header) is not one.
_SPEAKER = re.compile(r"\[([A-Za-z][\w.'-]{0,30})\][ \t]*")
# An auto-loaded skill injects its whole SKILL.md ahead of the typed text;
# the injected block always closes with this sentence (126 of 126 rows).
_SKILL_HEADER = re.compile(r'\[IMPORTANT: The "[^"\n]+" skill is auto-loaded\.')
_SKILL_END = "then run them with the terminal tool using the absolute path."

# Left at the front after the wrappers go: the agent talking to itself
# (retry notices, background-process and delegation events, compaction
# summaries).
_MACHINE_OPENERS = re.compile(
    r"\[(?:IMPORTANT:|System:|CONTEXT COMPACTION\b|PRIOR CONTEXT\b|ASYNC DELEGATION\b)"
)


def _bracket_end(text: str) -> int | None:
    """Index just past the ']' that closes the '[' at text[0]."""
    depth = 0
    for i, ch in enumerate(text):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return i + 1
    return None


def unwrap(content: str) -> tuple[str, list[str], bool]:
    """Strip Hermes gateway wrappers from a user row.

    Returns (typed text, speaker labels seen, cut) where cut is True when a
    wrapper had no recognisable end, so the typed text can't be isolated.
    """
    text = content.strip()
    speakers: list[str] = []
    while True:
        before = text
        if m := _REPLYING_TO.match(text) or _THREAD_CONTEXT.match(text):
            text = text[m.end() :]
        elif text.startswith(_NOTICE_OPENERS):
            end = _bracket_end(text)
            if end is None:
                return "", speakers, True
            text = text[end:]
        elif _SKILL_HEADER.match(text):
            end = text.find(_SKILL_END)
            if end < 0:
                return "", speakers, True
            text = text[end + len(_SKILL_END) :]
        elif m := _SPEAKER.match(text):
            speakers.append(m.group(1))
            text = text[m.end() :]
        text = text.strip()
        if text == before:
            return text, speakers, False


def _owner(conn: sqlite3.Connection) -> str | None:
    """The dominant Slack speaker label: whose prompts these are.

    Other labels ("[robin]" in a shared channel) are someone else typing.
    Counted over every row, not the sync window, so an incremental sync
    can't flip the owner.
    """
    counts: Counter[str] = Counter()
    for (content,) in conn.execute(
        "SELECT content FROM messages WHERE role = 'user' AND content LIKE '%[%] %'"
    ):
        counts.update(unwrap(content)[1])
    return counts.most_common(1)[0][0] if counts else None


class HermesStore:
    kind = SourceKind.HERMES
    # Bump when iter_prompts changes what it yields for rows already cached;
    # the cache re-reads the whole store on a mismatch. 2: gateway wrappers.
    parser_version = 2

    def __init__(self, db_path: Path | None = None):
        self.db_path = (db_path or Path("~/.hermes/state.db")).expanduser()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(f"file:{self.db_path}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        return conn

    def discover(self) -> StoreInfo:
        if not self.db_path.is_file():
            return StoreInfo(kind=self.kind, path=self.db_path, available=False, detail="not found")
        try:
            with self._connect() as conn:
                sessions = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                prompts = conn.execute(
                    "SELECT COUNT(*) FROM messages"
                    " WHERE role='user' AND active=1 AND compacted=0"
                    " AND content IS NOT NULL AND TRIM(content) != ''"
                ).fetchone()[0]
        except sqlite3.Error as exc:
            return StoreInfo(
                kind=self.kind, path=self.db_path, available=False, detail=type(exc).__name__
            )
        return StoreInfo(
            kind=self.kind,
            path=self.db_path,
            available=True,
            session_count=sessions,
            prompt_count=prompts,
        )

    def iter_prompts(self, since: datetime | None = None) -> Iterator[Prompt]:
        if not self.db_path.is_file():
            return
        since_epoch = since.timestamp() if since else 0.0
        with self._connect() as conn:
            owner = _owner(conn)
            for row in conn.execute(_PROMPT_QUERY, {"since": since_epoch}):
                raw = row["content"]
                if not raw.strip():
                    continue
                typed, speakers, cut = unwrap(raw)
                if owner and any(s != owner for s in speakers):
                    continue  # someone else's message in a shared channel
                machine = (
                    cut
                    or row["source"] in _MACHINE_SOURCES
                    or bool(_MACHINE_OPENERS.match(typed))
                    or classify_origin(typed) is PromptOrigin.MACHINE
                )
                if not machine and not typed:
                    continue  # attachment or quote only: nothing was typed
                content = raw if machine else typed
                yield Prompt(
                    source=self.kind,
                    session_id=row["session_id"],
                    message_ref=str(row["id"]),
                    content=content,
                    content_hash=content_hash(content),
                    timestamp=datetime.fromtimestamp(row["timestamp"], tz=UTC),
                    origin=PromptOrigin.MACHINE if machine else PromptOrigin.HUMAN,
                    cwd=row["cwd"],
                    git_repo=row["git_repo_root"],
                    model=row["model"],
                )

    def session_titles(self, since: datetime | None = None) -> list[tuple[str, str]]:
        """(iso-date, title) pairs for the report's session list."""
        if not self.db_path.is_file():
            return []
        since_epoch = since.timestamp() if since else 0.0
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT started_at, title FROM sessions"
                    " WHERE title IS NOT NULL AND started_at >= ?"
                    " ORDER BY started_at DESC",
                    (since_epoch,),
                ).fetchall()
        except sqlite3.Error:
            return []
        return [
            (datetime.fromtimestamp(r["started_at"], tz=UTC).date().isoformat(), r["title"])
            for r in rows
        ]

    def has_usage_table(self) -> bool:
        if not self.db_path.is_file():
            return False
        with self._connect() as conn:
            return bool(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='session_model_usage'"
                ).fetchone()
            )

    def iter_usage(
        self, since: datetime | None = None, session_id: str | None = None
    ) -> Iterator[UsageRecord]:
        """Per-session, per-model totals from `session_model_usage` (verified
        2026-10-10). Rows with a non-empty `task` (title_generation,
        background_review, vision, approval) and sub-agent sessions
        (`parent_session_id` set) are overhead, folded into the parent
        session. Hermes's own `estimated_cost_usd` (or `actual_cost_usd` when
        recorded) is carried as the record's cost when `cost_status` says it
        is real; otherwise the price table decides. No table: no usage."""
        if not self.has_usage_table():
            return
        with self._connect() as conn:
            sql = """
                SELECT u.session_id, u.model, u.billing_provider, u.billing_base_url,
                       u.billing_mode, u.task, u.api_call_count, u.input_tokens,
                       u.output_tokens, u.cache_read_tokens, u.cache_write_tokens,
                       u.estimated_cost_usd, u.actual_cost_usd, u.cost_status,
                       u.first_seen, u.last_seen, s.cwd, s.parent_session_id
                FROM session_model_usage u LEFT JOIN sessions s ON s.id = u.session_id
            """
            where, args = [], []
            if since is not None:
                where.append("u.last_seen >= ?")
                args.append(since.timestamp())
            if session_id is not None:  # the live nudge: this session and its children
                where.append("(u.session_id = ? OR s.parent_session_id = ?)")
                args += [session_id, session_id]
            if where:
                sql += " WHERE " + " AND ".join(where)
            for row in conn.execute(sql, args):
                last = row["last_seen"] or row["first_seen"]
                if last is None:
                    continue
                first = row["first_seen"] or last
                parent = row["parent_session_id"]
                yield UsageRecord(
                    source=self.kind,
                    session_id=parent or row["session_id"],
                    record_key="|".join(
                        str(row[k] or "")
                        for k in (
                            "session_id",
                            "model",
                            "billing_provider",
                            "billing_base_url",
                            "billing_mode",
                            "task",
                        )
                    ),
                    first_ts=datetime.fromtimestamp(first, tz=UTC),
                    last_ts=datetime.fromtimestamp(last, tz=UTC),
                    model=row["model"] or None,
                    calls=row["api_call_count"] or 0,
                    input_tokens=row["input_tokens"] or 0,
                    cache_read_tokens=row["cache_read_tokens"] or 0,
                    cache_write_tokens=row["cache_write_tokens"] or 0,
                    output_tokens=row["output_tokens"] or 0,
                    cwd=row["cwd"],
                    overhead=bool(row["task"]) or bool(parent),
                    store_cost_usd=_hermes_cost(row),
                )


def _hermes_cost(row: sqlite3.Row) -> float | None:
    """Hermes's own figure when it has one: actual beats estimated. "unknown"
    status (custom/local endpoints) means no figure -- the price table decides."""
    if (row["actual_cost_usd"] or 0) > 0:
        return float(row["actual_cost_usd"])
    if row["cost_status"] == "estimated":
        return float(row["estimated_cost_usd"] or 0.0)
    return None
