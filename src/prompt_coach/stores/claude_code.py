"""Claude Code session store: streaming reader for ~/.claude/projects/*/*.jsonl.

The largest corpus (hundreds of transcripts, hundreds of MB), so files are
streamed line by line and never loaded whole. A line is accepted as a prompt
only when ALL filters in BLUEPRINT.md 4.3 hold; everything else (tool
results, hook output, subagent traffic, command echoes, system reminders,
malformed lines) is skipped. iter_file yields byte offsets so the cache can
resume grown (append-only) files incrementally.

promptSource accepts "sdk" as well as "typed" (verified live 2026-09-24,
this machine, 277,872 real JSONL lines): "typed" does not appear in a
single current transcript -- Claude Code's own wire format moved on, and
the "typed"-only filter was accepting zero Claude Code prompts. Widening
to "sdk" recovers 4,359 real prompts, but "sdk" is not itself proof of
human authorship: 143 of those lines were harness-wrapped content
(<bash-input>/<bash-stdout>/<system-reminder>/<artifact-view-context>)
that origin.kind=human doesn't catch either. is_harness_wrapped
(stores/base.py) below closes that gap; without it this widening would
have poured that contamination straight into the human corpus.

Model capture (2026-07-29): the model that answered a turn lives on the
*following* `type: "assistant"` line's `message.model`, linked back via
`parentUuid` -- confirmed live that sessions genuinely mix models
(`/model` switches mid-conversation), so this is per-turn, not
per-session. A prompt is held pending until either a matching assistant
reply is seen or the next prompt/EOF arrives, at which point it is
flushed (with model=None if nothing matched). `<synthetic>` is a
placeholder value Claude Code emits for some internal turns, not a real
model, and is filtered out at the source.

Reply linking (2026-10-10): Claude Code now writes `type: "attachment"`
lines (hook output, context) between a prompt and its reply, and the
reply's parentUuid points at the last of them. Linking on the direct
parent matched 4 of 2,171 recent prompts; every other reply was reachable
through the parentUuid chain. iter_file now follows that chain: any line
whose parent is the pending prompt or one of its descendants joins it,
and the first assistant line whose parent is in the chain is the reply.

Usage (2026-10-10): every `type: "assistant"` line carries `message.usage`,
but a streamed reply is written several times with the same `requestId`
(51% of 137k usage lines on the maintainer's machine). Usage is keyed by
request id and the last line wins: where duplicates differ, the last one
has the final output count. Sub-agent transcripts live in
`<project>/<session>/subagents/*.jsonl` and carry the parent's sessionId,
so their calls fold into the parent session as overhead.
"""

from __future__ import annotations

import dataclasses
import json
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.models import Prompt, SourceKind, StoreInfo, UsageRecord
from prompt_coach.stores.base import classify_origin, content_hash, is_harness_wrapped

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
    if not text or is_harness_wrapped(text):
        # A whole-system-reminder line strips to empty above and is caught
        # by `not text`; other wrapper shapes (<bash-input>, <artifact-
        # view-context>, ...) aren't system-reminder-tagged so they survive
        # the strip -- is_harness_wrapped catches those (confirmed live
        # 2026-09-24, see the module docstring).
        return None
    return text


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
    if obj.get("promptSource") not in ("typed", "sdk"):
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


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def parse_usage_line(line: str, *, in_subagent_file: bool = False) -> UsageRecord | None:
    """One assistant line's token usage, or None when it carries none. Counts,
    model and folder only: message content is never read into the record."""
    if '"usage"' not in line:
        return None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or obj.get("type") != "assistant":
        return None
    message = obj.get("message") or {}
    usage = message.get("usage") if isinstance(message, dict) else None
    model = message.get("model") if isinstance(message, dict) else None
    if not isinstance(usage, dict) or model == "<synthetic>":
        return None
    key = obj.get("requestId") or message.get("id")
    session_id = obj.get("sessionId")
    raw_ts = obj.get("timestamp")
    if not key or not session_id or not isinstance(raw_ts, str):
        return None
    try:
        ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    split = usage.get("cache_creation")
    if isinstance(split, dict):
        write_5m = _int(split.get("ephemeral_5m_input_tokens"))
        write_1h = _int(split.get("ephemeral_1h_input_tokens"))
    else:
        write_5m, write_1h = _int(usage.get("cache_creation_input_tokens")), 0
    return UsageRecord(
        source=SourceKind.CLAUDE_CODE,
        session_id=str(session_id),
        record_key=str(key),
        first_ts=ts,
        last_ts=ts,
        model=model if isinstance(model, str) else None,
        calls=1,
        input_tokens=_int(usage.get("input_tokens")),
        cache_read_tokens=_int(usage.get("cache_read_input_tokens")),
        cache_write_tokens=write_5m,
        cache_write_1h_tokens=write_1h,
        output_tokens=_int(usage.get("output_tokens")),
        cwd=obj.get("cwd") if isinstance(obj.get("cwd"), str) else None,
        overhead=bool(obj.get("isSidechain")) or in_subagent_file,
    )


def _link_fields(line: str) -> tuple[str | None, str | None, str | None, str | None]:
    """(type, uuid, parentUuid, model) of a transcript line, or Nones. The
    model is only returned for assistant lines carrying a real one."""
    if '"parentUuid"' not in line:
        return None, None, None, None
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return None, None, None, None
    if not isinstance(obj, dict):
        return None, None, None, None
    kind = obj.get("type")
    model = (obj.get("message") or {}).get("model") if kind == "assistant" else None
    if not isinstance(model, str) or model == "<synthetic>":
        model = None
    return kind, obj.get("uuid"), obj.get("parentUuid"), model


class ClaudeCodeStore:
    kind = SourceKind.CLAUDE_CODE
    # 2: reply linking follows the parentUuid chain. Never purge on a bump:
    # Claude Code deletes transcripts after ~30 days, so the cache holds
    # prompts whose files are gone. The re-read fills fields in place.
    parser_version = 2
    purge_on_reparse = False

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
        append-only files exactly where the previous sync stopped. A prompt
        is held back one step (`pending`) so its model can be attached from
        the assistant reply that follows it -- see the module docstring.
        The held prompt's own offset is preserved (not shifted to wherever
        it gets flushed) so resume semantics are unchanged.
        """
        pending: tuple[int, Prompt] | None = None
        chain: set[str] = set()  # the pending prompt's uuid and its descendants
        with open(path, "rb") as f:
            if from_offset:
                f.seek(from_offset)
            offset = from_offset
            for raw in f:
                offset += len(raw)
                text = raw.decode("utf-8", errors="replace")
                prompt = parse_line(text)
                if prompt is not None:
                    if pending is not None:
                        yield pending
                    pending = (offset, prompt)
                    chain = {prompt.message_ref}
                    continue
                if pending is None:
                    continue
                kind, uuid, parent, model = _link_fields(text)
                if parent not in chain:
                    continue
                if kind == "assistant" and model is not None:
                    yield pending[0], dataclasses.replace(pending[1], model=model)
                    pending, chain = None, set()
                elif uuid:
                    chain.add(uuid)
            if pending is not None:
                yield pending

    def iter_usage_files(self) -> Iterator[Path]:
        """Main transcripts plus sub-agent transcripts (prompts skip the latter)."""
        yield from self.iter_files()
        yield from sorted(self.projects_dir.glob("*/*/subagents/*.jsonl"))

    def iter_usage_file(
        self, path: Path, from_offset: int = 0
    ) -> Iterator[tuple[int, UsageRecord]]:
        """Yield (byte_offset_after_line, record), resumable like iter_file.
        Duplicates are not collapsed here: the cache upserts on
        (session, request id), so the last line wins across syncs too."""
        in_subagent_file = path.parent.name == "subagents"
        with open(path, "rb") as f:
            if from_offset:
                f.seek(from_offset)
            offset = from_offset
            for raw in f:
                offset += len(raw)
                record = parse_usage_line(
                    raw.decode("utf-8", errors="replace"), in_subagent_file=in_subagent_file
                )
                if record is not None:
                    yield offset, record

    def iter_prompts(self, since: datetime | None = None) -> Iterator[Prompt]:
        for path in self.iter_files():
            for _, prompt in self.iter_file(path):
                if since is None or prompt.timestamp >= since:
                    yield prompt
