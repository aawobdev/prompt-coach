"""ChatGPT data-export store.

ChatGPT has no local session store; input is the official data-export ZIP
(or its extracted conversations.json). Built against the documented export
format and marked UNVERIFIED in STATUS.md until run against a real export:
a list of conversations, each with title, create_time, and a `mapping` tree
whose nodes carry message.author.role / message.content.content_type /
message.content.parts. Only visible, text-type user messages become prompts.
"""

from __future__ import annotations

import json
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.models import Prompt, SourceKind, StoreInfo
from prompt_coach.stores.base import classify_origin, content_hash


def looks_like_chatgpt_export(data: object) -> bool:
    """Shape probe used by the import command's format auto-detection."""
    return (
        isinstance(data, list)
        and len(data) > 0
        and isinstance(data[0], dict)
        and "mapping" in data[0]
    )


def _node_to_text(node: dict) -> str | None:
    message = node.get("message")
    if not isinstance(message, dict):
        return None
    if (message.get("author") or {}).get("role") != "user":
        return None
    if (message.get("metadata") or {}).get("is_visually_hidden_from_conversation"):
        return None
    content = message.get("content") or {}
    if content.get("content_type") != "text":
        return None
    parts = [p for p in content.get("parts", []) if isinstance(p, str)]
    text = "\n".join(parts).strip()
    return text or None


def _message_time(node: dict, fallback: datetime) -> datetime:
    ct = (node.get("message") or {}).get("create_time")
    if isinstance(ct, int | float):
        return datetime.fromtimestamp(ct, tz=UTC)
    return fallback


class ChatGPTExportStore:
    kind = SourceKind.CHATGPT

    def __init__(self, file_path: Path):
        self.file_path = Path(file_path).expanduser()

    def _load(self) -> list[dict]:
        if self.file_path.suffix.lower() == ".zip":
            with zipfile.ZipFile(self.file_path) as zf:
                name = next((n for n in zf.namelist() if n.endswith("conversations.json")), None)
                if name is None:
                    raise FileNotFoundError("no conversations.json in export zip")
                data = json.loads(zf.read(name))
        else:
            with open(self.file_path) as f:
                data = json.load(f)
        return data if isinstance(data, list) else []

    def discover(self) -> StoreInfo:
        if not self.file_path.is_file():
            return StoreInfo(
                kind=self.kind, path=self.file_path, available=False, detail="not found"
            )
        try:
            conversations = self._load()
        except (json.JSONDecodeError, OSError, zipfile.BadZipFile, FileNotFoundError) as exc:
            return StoreInfo(
                kind=self.kind, path=self.file_path, available=False, detail=type(exc).__name__
            )
        prompts = sum(1 for _ in self._iter_all(conversations))
        return StoreInfo(
            kind=self.kind,
            path=self.file_path,
            available=True,
            session_count=len(conversations),
            prompt_count=prompts,
        )

    def _iter_all(self, conversations: list[dict]) -> Iterator[Prompt]:
        for i, conv in enumerate(conversations):
            if not isinstance(conv, dict):
                continue
            conv_id = str(conv.get("conversation_id") or conv.get("id") or f"conv-{i}")
            fallback = (
                datetime.fromtimestamp(conv["create_time"], tz=UTC)
                if isinstance(conv.get("create_time"), int | float)
                else datetime.fromtimestamp(0, tz=UTC)
            )
            mapping = conv.get("mapping")
            if not isinstance(mapping, dict):
                continue
            nodes = []
            for node_id, node in mapping.items():
                if not isinstance(node, dict):
                    continue
                text = _node_to_text(node)
                if text is not None:
                    nodes.append((_message_time(node, fallback), node_id, text))
            for ts, node_id, text in sorted(nodes):
                yield Prompt(
                    source=self.kind,
                    session_id=conv_id,
                    message_ref=node_id,
                    content=text,
                    content_hash=content_hash(text),
                    timestamp=ts,
                    origin=classify_origin(text),
                )

    def iter_prompts(self, since: datetime | None = None) -> Iterator[Prompt]:
        for prompt in self._iter_all(self._load()):
            if since is None or prompt.timestamp >= since:
                yield prompt
