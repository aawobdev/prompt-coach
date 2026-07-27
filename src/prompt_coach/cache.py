"""Local cache DB: the keystone between stores and analysis.

Parses each source store once, resyncs incrementally, dedupes session forks,
maintains an FTS5 index for `query`, and memoises LLM results. Lives under
~/.cache/prompt-coach/ (0700/0600: it contains prompt content) and is fully
disposable: deleting the directory erases every derived artifact.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.models import Prompt, PromptOrigin, SourceKind, SyncStats
from prompt_coach.stores.base import SessionStore

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prompts (
    source TEXT NOT NULL,
    session_id TEXT NOT NULL,
    message_ref TEXT NOT NULL,
    content TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    timestamp REAL NOT NULL,
    origin TEXT NOT NULL,
    cwd TEXT,
    git_repo TEXT,
    PRIMARY KEY (source, session_id, message_ref)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prompts_fork_dedupe
    ON prompts(content_hash, timestamp);
CREATE INDEX IF NOT EXISTS idx_prompts_time ON prompts(timestamp);
CREATE TABLE IF NOT EXISTS file_state (
    path TEXT PRIMARY KEY,
    mtime REAL NOT NULL,
    size INTEGER NOT NULL,
    offset INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS llm_cache (
    key TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    model TEXT NOT NULL,
    template_version TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS prompts_fts USING fts5(
    content, content='prompts', content_rowid='rowid'
);
"""


class CacheDB:
    def __init__(self, path: Path | None = None):
        if path is None:
            xdg = os.environ.get("XDG_CACHE_HOME", "~/.cache")
            path = Path(xdg).expanduser() / "prompt-coach" / "cache.db"
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        existed = self.path.exists()
        self.conn = sqlite3.connect(self.path)
        if not existed:
            os.chmod(self.path, 0o600)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # -- sync ---------------------------------------------------------------

    def sync(
        self,
        stores: Sequence[SessionStore],
        force: bool = False,
        on_store: Callable[[str, bool], None] | None = None,
    ) -> SyncStats:
        """`on_store(kind, done)` fires (False) right before a store starts and
        (True) right after -- lets a caller show per-store progress. One store
        (e.g. Copilot's /mnt/c reads) can dominate wall clock; without this, the
        CLI looks hung between the initial invocation and the final render."""
        stats = SyncStats()
        for store in stores:
            if on_store:
                on_store(store.kind.value, False)
            info = store.discover()
            if not info.available:
                stats.stores_failed[store.kind.value] = info.detail or "unavailable"
            else:
                try:
                    if hasattr(store, "iter_files"):
                        self._sync_by_files(store, stats, force)
                    else:
                        self._sync_by_watermark(store, stats, force)
                except Exception as exc:  # noqa: BLE001 - one bad store must not kill sync
                    stats.stores_failed[store.kind.value] = type(exc).__name__
            if on_store:
                on_store(store.kind.value, True)
        self.conn.commit()
        return stats

    def _insert(self, p: Prompt, stats: SyncStats) -> None:
        stats.scanned += 1
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO prompts VALUES (?,?,?,?,?,?,?,?,?)",
            (
                p.source.value,
                p.session_id,
                p.message_ref,
                p.content,
                p.content_hash,
                p.timestamp.timestamp(),
                p.origin.value,
                p.cwd,
                p.git_repo,
            ),
        )
        if cur.rowcount:
            stats.added += 1
            self.conn.execute(
                "INSERT INTO prompts_fts(rowid, content) VALUES (?,?)",
                (cur.lastrowid, p.content),
            )
        else:
            stats.deduped += 1

    def _sync_by_files(self, store, stats: SyncStats, force: bool) -> None:
        """Per-file incremental sync for file-based stores (Claude Code JSONL)."""
        for fpath in store.iter_files():
            st = fpath.stat()
            key = str(fpath)
            row = (
                None
                if force
                else self.conn.execute(
                    "SELECT mtime, size, offset FROM file_state WHERE path=?", (key,)
                ).fetchone()
            )
            offset = 0
            if row is not None:
                if row["mtime"] == st.st_mtime and row["size"] == st.st_size:
                    continue  # unchanged
                if st.st_size >= row["size"]:
                    offset = row["offset"]  # append-only growth: resume
                # shrunk/rewritten: offset stays 0, upserts are idempotent
            last_offset = offset
            for line_offset, prompt in store.iter_file(fpath, from_offset=offset):
                self._insert(prompt, stats)
                last_offset = line_offset
            self.conn.execute(
                "INSERT OR REPLACE INTO file_state VALUES (?,?,?,?)",
                (key, st.st_mtime, st.st_size, last_offset),
            )

    def _sync_by_watermark(self, store, stats: SyncStats, force: bool) -> None:
        """Timestamp-watermark sync for DB-backed stores (Hermes)."""
        key = f"watermark:{store.kind.value}"
        since = None
        if not force:
            row = self.conn.execute("SELECT offset FROM file_state WHERE path=?", (key,)).fetchone()
            if row is not None:
                since = datetime.fromtimestamp(row["offset"] / 1000.0, tz=UTC)
        latest = since.timestamp() if since else 0.0
        for prompt in store.iter_prompts(since=since):
            self._insert(prompt, stats)
            latest = max(latest, prompt.timestamp.timestamp())
        self.conn.execute(
            "INSERT OR REPLACE INTO file_state VALUES (?,?,?,?)",
            (key, 0.0, 0, int(latest * 1000)),
        )

    # -- queries ------------------------------------------------------------

    @staticmethod
    def _row_to_prompt(row: sqlite3.Row) -> Prompt:
        return Prompt(
            source=SourceKind(row["source"]),
            session_id=row["session_id"],
            message_ref=row["message_ref"],
            content=row["content"],
            content_hash=row["content_hash"],
            timestamp=datetime.fromtimestamp(row["timestamp"], tz=UTC),
            origin=PromptOrigin(row["origin"]),
            cwd=row["cwd"],
            git_repo=row["git_repo"],
        )

    def prompts(
        self,
        *,
        since: datetime | None = None,
        origin: PromptOrigin | None = None,
        source: SourceKind | None = None,
        limit: int | None = None,
    ) -> list[Prompt]:
        sql = "SELECT * FROM prompts WHERE 1=1"
        args: list = []
        if since is not None:
            sql += " AND timestamp >= ?"
            args.append(since.timestamp())
        if origin is not None:
            sql += " AND origin = ?"
            args.append(origin.value)
        if source is not None:
            sql += " AND source = ?"
            args.append(source.value)
        sql += " ORDER BY timestamp"
        if limit is not None:
            sql += " LIMIT ?"
            args.append(limit)
        return [self._row_to_prompt(r) for r in self.conn.execute(sql, args)]

    def counts(self, since: datetime | None = None) -> dict[str, int]:
        sql = "SELECT source, COUNT(*) n, COUNT(DISTINCT session_id) s FROM prompts"
        args: list = []
        if since is not None:
            sql += " WHERE timestamp >= ?"
            args.append(since.timestamp())
        sql += " GROUP BY source"
        out: dict[str, int] = {"prompts": 0, "sessions": 0}
        for row in self.conn.execute(sql, args):
            out[row["source"]] = row["n"]
            out["prompts"] += row["n"]
            out["sessions"] += row["s"]
        return out

    def search(self, query: str, limit: int = 20) -> list[Prompt]:
        """FTS5 search; the query is quoted so user text is never FTS syntax."""
        phrase = '"' + query.replace('"', '""') + '"'
        try:
            rows = self.conn.execute(
                "SELECT p.* FROM prompts_fts f JOIN prompts p ON p.rowid = f.rowid"
                " WHERE prompts_fts MATCH ? ORDER BY rank LIMIT ?",
                (phrase, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = []
        if not rows:  # phrase too strict or FTS hiccup: token AND fallback
            tokens = [t for t in query.split() if t.isalnum()]
            if tokens:
                try:
                    rows = self.conn.execute(
                        "SELECT p.* FROM prompts_fts f JOIN prompts p ON p.rowid = f.rowid"
                        " WHERE prompts_fts MATCH ? ORDER BY rank LIMIT ?",
                        (" ".join(tokens), limit),
                    ).fetchall()
                except sqlite3.OperationalError:
                    rows = []
        return [self._row_to_prompt(r) for r in rows]

    # -- llm memoisation ----------------------------------------------------

    def get_llm(self, key: str) -> dict | None:
        row = self.conn.execute("SELECT payload FROM llm_cache WHERE key=?", (key,)).fetchone()
        return json.loads(row["payload"]) if row else None

    def put_llm(self, key: str, payload: dict, model: str, template_version: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO llm_cache VALUES (?,?,?,?,?)",
            (
                key,
                json.dumps(payload),
                model,
                template_version,
                datetime.now(tz=UTC).timestamp(),
            ),
        )
        self.conn.commit()

    def llm_cache_stats(self) -> dict[str, int]:
        row = self.conn.execute("SELECT COUNT(*) n FROM llm_cache").fetchone()
        return {"entries": row["n"]}

    def clear(self) -> None:
        self.conn.executescript(
            "DELETE FROM prompts; DELETE FROM prompts_fts;"
            " DELETE FROM file_state; DELETE FROM llm_cache;"
        )
        self.conn.commit()
