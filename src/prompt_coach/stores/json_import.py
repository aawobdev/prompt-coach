"""Generic JSON session import (format in BLUEPRINT.md 4.4)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.models import Prompt, SourceKind, StoreInfo
from prompt_coach.stores.base import classify_origin, content_hash


def _parse_timestamp(value: object) -> datetime:
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=UTC)
    if isinstance(value, str):
        try:
            ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)
        except ValueError:
            pass
    return datetime.fromtimestamp(0, tz=UTC)


class JsonImportStore:
    kind = SourceKind.JSON_IMPORT

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path).expanduser()

    def discover(self) -> StoreInfo:
        if not self.file_path.is_file():
            return StoreInfo(
                kind=self.kind, path=self.file_path, available=False, detail="not found"
            )
        try:
            sessions = self._load()
        except (json.JSONDecodeError, OSError) as exc:
            return StoreInfo(
                kind=self.kind, path=self.file_path, available=False, detail=type(exc).__name__
            )
        prompts = sum(
            1
            for s in sessions
            for m in s.get("messages", [])
            if m.get("role") == "user" and m.get("content")
        )
        return StoreInfo(
            kind=self.kind,
            path=self.file_path,
            available=True,
            session_count=len(sessions),
            prompt_count=prompts,
        )

    def _load(self) -> list[dict]:
        with open(self.file_path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []

    def iter_prompts(self, since: datetime | None = None) -> Iterator[Prompt]:
        for session in self._load():
            session_id = str(session.get("session_id", "import"))
            ts = _parse_timestamp(session.get("timestamp"))
            if since is not None and ts < since:
                continue
            user_idx = 0
            for msg in session.get("messages", []):
                if msg.get("role") != "user":
                    continue
                content = msg.get("content") or ""
                if not content.strip():
                    continue
                yield Prompt(
                    source=self.kind,
                    session_id=session_id,
                    message_ref=f"{session_id}:{user_idx}",
                    content=content,
                    content_hash=content_hash(content),
                    timestamp=ts,
                    origin=classify_origin(content),
                )
                user_idx += 1
