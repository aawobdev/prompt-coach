"""Copilot Chat session store (VS Code workspaceStorage).

Format verified against live files 2026-07-20 (BLUEPRINT.md 16.1). Each
session file is a JSON-patch event log; prompts appear in exactly two event
shapes: the kind-0 initial state (v.requests[]) and kind-2 appends where the
patch path is ["requests"]. kind-1 events mutate existing paths and never
carry new prompts, so byte-offset resume over the append-only file is valid.
The session id equals the filename stem, so resumed parses never need to
re-read the kind-0 line.

On WSL the Windows VS Code profile is read directly via /mnt/c; a native
Linux profile is probed as a fallback.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.models import Prompt, SourceKind, StoreInfo
from prompt_coach.stores.base import classify_origin, content_hash


def default_candidates() -> list[Path]:
    candidates = [
        p / "AppData/Roaming/Code/User/workspaceStorage"
        for p in sorted(Path("/mnt/c/Users").glob("*"))
        if p.name not in ("Public", "Default", "Default User", "All Users")
    ]
    candidates.append(Path("~/.config/Code/User/workspaceStorage").expanduser())
    return candidates


def _requests_from_line(obj: dict) -> list[dict]:
    """The two event shapes that carry prompts; everything else is empty."""
    kind = obj.get("kind")
    if kind == 0:
        v = obj.get("v")
        if isinstance(v, dict) and isinstance(v.get("requests"), list):
            return [r for r in v["requests"] if isinstance(r, dict)]
    elif kind == 2 and obj.get("k") == ["requests"]:
        v = obj.get("v")
        if isinstance(v, list):
            return [r for r in v if isinstance(r, dict)]
    return []


def _request_to_prompt(req: dict, session_id: str) -> Prompt | None:
    message = req.get("message")
    text = message.get("text", "") if isinstance(message, dict) else ""
    if not text.strip():
        return None
    ts_ms = req.get("timestamp")
    if not isinstance(ts_ms, int | float):
        return None
    return Prompt(
        source=SourceKind.COPILOT,
        session_id=session_id,
        message_ref=str(req.get("requestId") or content_hash(text)[:16]),
        content=text,
        content_hash=content_hash(text),
        timestamp=datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC),
        origin=classify_origin(text),
    )


class CopilotStore:
    kind = SourceKind.COPILOT

    def __init__(self, storage_dir: Path | None = None):
        if storage_dir is not None:
            self.storage_dirs = [Path(storage_dir).expanduser()]
        else:
            self.storage_dirs = default_candidates()

    def _existing_dirs(self) -> list[Path]:
        return [d for d in self.storage_dirs if d.is_dir()]

    def discover(self) -> StoreInfo:
        dirs = self._existing_dirs()
        shown = dirs[0] if dirs else self.storage_dirs[0]
        if not dirs:
            return StoreInfo(kind=self.kind, path=shown, available=False, detail="not found")
        n_files = sum(1 for _ in self.iter_files())
        return StoreInfo(
            kind=self.kind,
            path=shown,
            available=True,
            session_count=n_files,
            detail=f"{n_files} chat sessions",
        )

    def iter_files(self) -> Iterator[Path]:
        for storage in self._existing_dirs():
            yield from sorted(storage.glob("*/chatSessions/*.jsonl"))

    def iter_file(self, path: Path, from_offset: int = 0) -> Iterator[tuple[int, Prompt]]:
        session_id = path.stem
        with open(path, "rb") as f:
            if from_offset:
                f.seek(from_offset)
            offset = from_offset
            for raw in f:
                offset += len(raw)
                try:
                    obj = json.loads(raw.decode("utf-8", errors="replace"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                for req in _requests_from_line(obj):
                    prompt = _request_to_prompt(req, session_id)
                    if prompt is not None:
                        yield offset, prompt

    def iter_prompts(self, since: datetime | None = None) -> Iterator[Prompt]:
        for path in self.iter_files():
            for _, prompt in self.iter_file(path):
                if since is None or prompt.timestamp >= since:
                    yield prompt
