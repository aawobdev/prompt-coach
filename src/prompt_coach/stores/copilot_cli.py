"""GitHub Copilot CLI store: token usage from ~/.copilot/session-state/*/events.jsonl.

UNVERIFIED (2026-10-10): built from a reference parser written on another
machine; no Copilot CLI data existed where this was written. Check it
against real files before trusting the numbers (STATUS.md has the list).

Usage only -- prompts are not read yet, so this store adds cost data and
never adds to the prompt corpus. Not the `copilot` store, which reads VS
Code Copilot Chat's workspaceStorage.

Assumed format, one JSON event per line:
- `session.start`: `timestamp`, `data.context.cwd`
- `session.usage_checkpoint` / `session.shutdown`: `timestamp`,
  `data.modelMetrics.{model}.usage.{inputTokens, outputTokens,
  cacheReadTokens, cacheWriteTokens}` and `.requests.count`. inputTokens
  includes the cached tokens, so uncached input = input - read - write.
The last checkpoint or shutdown in the file is the session's running total.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from prompt_coach.models import Prompt, SourceKind, StoreInfo, UsageRecord

_TOTAL_EVENTS = ("session.usage_checkpoint", "session.shutdown")


def default_candidates() -> list[Path]:
    candidates = [
        p / ".copilot/session-state"
        for p in sorted(Path("/mnt/c/Users").glob("*"))
        if p.name not in ("Public", "Default", "Default User", "All Users")
    ]
    candidates.append(Path("~/.copilot/session-state").expanduser())
    return candidates


def _ts(raw: object) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _n(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def parse_events(path: Path) -> list[UsageRecord]:
    """One record per model from the file's last running-total event."""
    session_id = path.parent.name
    start: datetime | None = None
    cwd: str | None = None
    last_total: dict | None = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if '"session.' not in line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            data = event.get("data") if isinstance(event.get("data"), dict) else {}
            if kind == "session.start":
                start = _ts(event.get("timestamp"))
                context = data.get("context") if isinstance(data.get("context"), dict) else {}
                cwd = context.get("cwd") if isinstance(context.get("cwd"), str) else None
            elif kind in _TOTAL_EVENTS:
                last_total = event
    if last_total is None:
        return []
    end = _ts(last_total.get("timestamp"))
    if end is None:
        return []
    metrics = (last_total.get("data") or {}).get("modelMetrics") or {}
    records = []
    for model, values in metrics.items() if isinstance(metrics, dict) else ():
        usage = values.get("usage") if isinstance(values, dict) else None
        if not isinstance(usage, dict):
            continue
        requests = values.get("requests") if isinstance(values.get("requests"), dict) else {}
        read, write = _n(usage.get("cacheReadTokens")), _n(usage.get("cacheWriteTokens"))
        records.append(
            UsageRecord(
                source=SourceKind.COPILOT_CLI,
                session_id=session_id,
                record_key=str(model),
                first_ts=start or end,
                last_ts=end,
                model=str(model),
                calls=_n(requests.get("count")),
                input_tokens=max(_n(usage.get("inputTokens")) - read - write, 0),
                cache_read_tokens=read,
                cache_write_tokens=write,
                output_tokens=_n(usage.get("outputTokens")),
                cwd=cwd,
            )
        )
    return records


class CopilotCliStore:
    kind = SourceKind.COPILOT_CLI

    def __init__(self, state_dir: Path | None = None):
        self.state_dirs = [state_dir.expanduser()] if state_dir else default_candidates()

    def _files(self) -> Iterator[Path]:
        for state_dir in self.state_dirs:
            if state_dir.is_dir():
                yield from sorted(state_dir.glob("*/events.jsonl"))

    def discover(self) -> StoreInfo:
        found = [d for d in self.state_dirs if d.is_dir()]
        if not found:
            return StoreInfo(
                kind=self.kind, path=self.state_dirs[-1], available=False, detail="not found"
            )
        n = sum(1 for _ in self._files())
        return StoreInfo(
            kind=self.kind,
            path=found[0],
            available=True,
            session_count=n,
            prompt_count=0,
            detail=f"{n} sessions (usage only, unverified)",
        )

    def iter_prompts(self, since: datetime | None = None) -> Iterator[Prompt]:
        return iter(())  # usage-only store: see the module docstring

    def iter_usage(self, since: datetime | None = None) -> Iterator[UsageRecord]:
        """Each file is re-read when it changed since the last sync; the cache
        upserts per (session, model), so a growing session just updates."""
        for path in self._files():
            if since is not None and path.stat().st_mtime < since.timestamp():
                continue
            yield from parse_events(path)
