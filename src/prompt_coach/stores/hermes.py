"""Hermes session store: read-only access to ~/.hermes/state.db.

Schema facts verified against the live DB 2026-07-20 (see BLUEPRINT.md 4.2).
Hermes runs WAL and may write concurrently, so the DB is opened via a
mode=ro URI (not immutable=1) and every failure degrades to an unavailable
StoreInfo instead of raising.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.models import Prompt, SourceKind, StoreInfo
from prompt_coach.stores.base import classify_origin, content_hash

_PROMPT_QUERY = """
SELECT m.id, m.session_id, m.content, m.timestamp, s.cwd, s.git_repo_root
FROM messages m
JOIN sessions s ON s.id = m.session_id
WHERE m.role = 'user'
  AND m.active = 1
  AND m.compacted = 0
  AND m.content IS NOT NULL
  AND m.timestamp >= :since
ORDER BY m.timestamp
"""


class HermesStore:
    kind = SourceKind.HERMES

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
            for row in conn.execute(_PROMPT_QUERY, {"since": since_epoch}):
                content = row["content"]
                if not content.strip():
                    continue
                yield Prompt(
                    source=self.kind,
                    session_id=row["session_id"],
                    message_ref=str(row["id"]),
                    content=content,
                    content_hash=content_hash(content),
                    timestamp=datetime.fromtimestamp(row["timestamp"], tz=UTC),
                    origin=classify_origin(content),
                    cwd=row["cwd"],
                    git_repo=row["git_repo_root"],
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
