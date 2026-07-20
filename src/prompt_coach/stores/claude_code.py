"""Claude Code session store: streaming reader for ~/.claude/projects/*/*.jsonl.

The largest corpus (hundreds of transcripts, hundreds of MB), so files are
streamed line by line and never loaded whole. A line is accepted as a prompt
only when ALL filters in BLUEPRINT.md 4.3 hold; everything else (tool
results, hook output, subagent traffic, command echoes, system reminders,
malformed lines) is skipped. iter_file yields byte offsets so the cache can
resume grown (append-only) files incrementally.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.models import Prompt, SourceKind, StoreInfo
from prompt_coach.stores.base import classify_origin, content_hash

_SYSTEM_REMINDER = re.compile(r"<system-reminder>.*?</system-reminder>", re.DOTALL)
_COMMAND_MARKERS = ("<command-name>", "<local-command-stdout>", "<local-command-caveat>")


def _normalise_content(raw: object) -> str | None:
    """String or content-block-array to plain text; None when not a real prompt."""
    if isinstance(raw, str):
        text = raw
    elif isinstance(raw, list):
        parts = [
            block.get("text", "")
            for block in raw
            if isinstance(block, dict) and block.get("type") == "text"
        ]
        text = "\n".join(p for p in parts if p)
    else:
        return None
    if any(marker in text for marker in _COMMAND_MARKERS):
        return None
    text = _SYSTEM_REMINDER.sub("", text).strip()
    return text or None


def parse_line(line: str) -> Prompt | None:
    """Apply the five acceptance filters. Returns None for any rejected line."""
    line = line.strip()
    if not line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "user":
        return None
    if (obj.get("origin") or {}).get("kind") != "human":
        return None
    if obj.get("promptSource") != "typed":
        return None
    if obj.get("isSidechain"):
        return None
    content = _normalise_content((obj.get("message") or {}).get("content"))
    if content is None:
        return None
    try:
        ts = datetime.fromisoformat(obj["timestamp"].replace("Z", "+00:00"))
    except (KeyError, ValueError, AttributeError):
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return Prompt(
        source=SourceKind.CLAUDE_CODE,
        session_id=obj.get("sessionId", "unknown"),
        message_ref=obj.get("uuid") or content_hash(content)[:16],
        content=content,
        content_hash=content_hash(content),
        timestamp=ts.astimezone(UTC),
        origin=classify_origin(content),
        cwd=obj.get("cwd"),
        git_repo=obj.get("cwd"),
    )


class ClaudeCodeStore:
    kind = SourceKind.CLAUDE_CODE

    def __init__(self, projects_dir: Path | None = None):
        self.projects_dir = (projects_dir or Path("~/.claude/projects")).expanduser()

    def discover(self) -> StoreInfo:
        if not self.projects_dir.is_dir():
            return StoreInfo(
                kind=self.kind, path=self.projects_dir, available=False, detail="not found"
            )
        n_files = sum(1 for _ in self.iter_files())
        return StoreInfo(
            kind=self.kind,
            path=self.projects_dir,
            available=True,
            session_count=n_files,
            detail=f"{n_files} transcripts",
        )

    def iter_files(self) -> Iterator[Path]:
        yield from sorted(self.projects_dir.glob("*/*.jsonl"))

    def iter_file(self, path: Path, from_offset: int = 0) -> Iterator[tuple[int, Prompt]]:
        """Yield (byte_offset_after_line, prompt) from one transcript.

        Offsets are byte positions so the cache's file_state can resume
        append-only files exactly where the previous sync stopped.
        """
        with open(path, "rb") as f:
            if from_offset:
                f.seek(from_offset)
            offset = from_offset
            for raw in f:
                offset += len(raw)
                prompt = parse_line(raw.decode("utf-8", errors="replace"))
                if prompt is not None:
                    yield offset, prompt

    def iter_prompts(self, since: datetime | None = None) -> Iterator[Prompt]:
        for path in self.iter_files():
            for _, prompt in self.iter_file(path):
                if since is None or prompt.timestamp >= since:
                    yield prompt
