"""Codex CLI session store: ~/.codex/sessions/YYYY/MM/DD/rollout-*.jsonl.

Format observed live 2026-07-21 (single real session, VS Code extension
originator). Each line is `{"timestamp", "type", "payload"}`. Prompts come
from `type=event_msg` lines whose `payload.type == "user_message"` and
`payload.kind == "plain"`; `kind == "environment_context"` is IDE/sandbox
boilerplate injected every turn, never something the user typed, and is
dropped. The VS Code extension additionally wraps real requests in an
"IDE setup" block (active file, open tabs, files mentioned); when the
`## My request for Codex:` marker is present only the text after it is
kept, mirroring how the Claude Code store strips its own injected
boilerplate. No per-message id exists in the format, so message_ref falls
back to a content hash like the Copilot store. One file per session
(filename stem is a uuid), append-only, so byte-offset resume is valid.

On WSL the Windows profile is read directly via /mnt/c; a native Linux
profile is probed as a fallback.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.models import Prompt, SourceKind, StoreInfo
from prompt_coach.stores.base import classify_origin, content_hash

_REQUEST_MARKER = re.compile(r"##\s*My request for Codex:\s*\n?", re.IGNORECASE)


def default_candidates() -> list[Path]:
    candidates = [
        p / ".codex/sessions"
        for p in sorted(Path("/mnt/c/Users").glob("*"))
        if p.name not in ("Public", "Default", "Default User", "All Users")
    ]
    candidates.append(Path("~/.codex/sessions").expanduser())
    return candidates


def _extract_request(message: str) -> str:
    """Strip the VS Code IDE-context wrapper down to the actual request."""
    m = _REQUEST_MARKER.search(message)
    return message[m.end() :].strip() if m else message.strip()


def _line_to_prompt(obj: dict, session_id: str, cwd: str | None) -> Prompt | None:
    if obj.get("type") != "event_msg":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "user_message":
        return None
    if payload.get("kind") != "plain":
        return None
    text = _extract_request(payload.get("message", ""))
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(obj["timestamp"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return None
    return Prompt(
        source=SourceKind.CODEX,
        session_id=session_id,
        message_ref=content_hash(text)[:16],
        content=text,
        content_hash=content_hash(text),
        timestamp=ts.astimezone(UTC),
        origin=classify_origin(text),
        cwd=cwd,
        git_repo=cwd,
    )


class CodexStore:
    kind = SourceKind.CODEX

    def __init__(self, sessions_dir: Path | None = None):
        if sessions_dir is not None:
            self.sessions_dirs = [Path(sessions_dir).expanduser()]
        else:
            self.sessions_dirs = default_candidates()

    def _existing_dirs(self) -> list[Path]:
        return [d for d in self.sessions_dirs if d.is_dir()]

    def discover(self) -> StoreInfo:
        dirs = self._existing_dirs()
        shown = dirs[0] if dirs else self.sessions_dirs[0]
        if not dirs:
            return StoreInfo(kind=self.kind, path=shown, available=False, detail="not found")
        n_files = sum(1 for _ in self.iter_files())
        return StoreInfo(
            kind=self.kind,
            path=shown,
            available=True,
            session_count=n_files,
            detail=f"{n_files} sessions",
        )

    def iter_files(self) -> Iterator[Path]:
        for storage in self._existing_dirs():
            yield from sorted(storage.glob("*/*/*/rollout-*.jsonl"))

    def iter_file(self, path: Path, from_offset: int = 0) -> Iterator[tuple[int, Prompt]]:
        session_id = path.stem
        # session_meta (cwd) is always line 1; resumed reads (from_offset > 0)
        # start past it, so cwd is None on incrementally-synced prompts.
        cwd: str | None = None
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
                if obj.get("type") == "session_meta":
                    payload = obj.get("payload")
                    if isinstance(payload, dict) and isinstance(payload.get("cwd"), str):
                        cwd = payload["cwd"]
                    continue
                prompt = _line_to_prompt(obj, session_id, cwd)
                if prompt is not None:
                    yield offset, prompt

    def iter_prompts(self, since: datetime | None = None) -> Iterator[Prompt]:
        for path in self.iter_files():
            for _, prompt in self.iter_file(path):
                if since is None or prompt.timestamp >= since:
                    yield prompt
