"""Estimated spend from token usage: deterministic, local, no network.

Every figure is an estimate: list price (or the store's own estimate, e.g.
Hermes's) x `[cost] billed_fraction` x `usd_rate`. A record whose model has
no price stays in token counts and is reported as unpriced -- it is never
costed at another model's rate (DECISIONS.md 2026-10-10).
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePath

from prompt_coach.config import CostConfig
from prompt_coach.models import SessionUsage, SourceKind, UsageRecord
from prompt_coach.pricing import list_cost_usd, normalise_model, price_for

GROUP_KEYS = ("session", "folder", "model", "store", "day")


def record_cost(record: UsageRecord, cost: CostConfig) -> float | None:
    """One record's estimated cost in the configured currency, or None when
    neither the store nor the price table knows the model's price."""
    if record.store_cost_usd is not None:
        usd = record.store_cost_usd
    else:
        price = price_for(record.model, cost.prices)
        if price is None:
            return None
        usd = list_cost_usd(
            price,
            input_tokens=record.input_tokens,
            output_tokens=record.output_tokens,
            cache_read_tokens=record.cache_read_tokens,
            cache_write_tokens=record.cache_write_tokens,
            cache_write_1h_tokens=record.cache_write_1h_tokens,
        )
    return usd * cost.fraction_for(record.source.value) * cost.usd_rate


def folder_of(cwd: str | None) -> str:
    return PurePath(cwd).name or cwd if cwd else "(no folder)"


def aggregate_sessions(records: Iterable[UsageRecord], cost: CostConfig) -> list[SessionUsage]:
    """One SessionUsage per (source, session), most expensive first. Ties
    break on calls then session id, so output order is stable."""
    groups: dict[tuple[SourceKind, str], list[UsageRecord]] = defaultdict(list)
    for r in records:
        groups[(r.source, r.session_id)].append(r)
    sessions = [_session(source, sid, recs, cost) for (source, sid), recs in groups.items()]
    sessions.sort(key=lambda s: (-(s.est_cost or 0.0), -s.api_calls, s.source.value, s.session_id))
    return sessions


def _session(
    source: SourceKind, session_id: str, recs: list[UsageRecord], cost: CostConfig
) -> SessionUsage:
    by_model: Counter[str] = Counter()
    total = overhead_cost = 0.0
    priced = False
    unpriced = overhead_calls = 0
    for r in recs:
        if r.model:
            by_model[r.model] += r.calls
        c = record_cost(r, cost)
        if c is None:
            unpriced += r.calls
        else:
            priced = True
            total += c
            if r.overhead:
                overhead_cost += c
        if r.overhead:
            overhead_calls += r.calls
    models = tuple(sorted(by_model.items(), key=lambda kv: (-kv[1], kv[0])))
    return SessionUsage(
        source=source,
        session_id=session_id,
        model=models[0][0] if models else None,
        api_calls=sum(r.calls for r in recs),
        input_tokens=sum(r.input_tokens for r in recs),
        cache_read_tokens=sum(r.cache_read_tokens for r in recs),
        cache_write_tokens=sum(r.cache_write_tokens + r.cache_write_1h_tokens for r in recs),
        output_tokens=sum(r.output_tokens for r in recs),
        first_ts=min(r.first_ts for r in recs),
        last_ts=max(r.last_ts for r in recs),
        cwd=next((r.cwd for r in recs if r.cwd), None),
        max_gap_seconds=_max_gap(recs),
        models=models,
        overhead_calls=overhead_calls,
        est_cost=total if priced else None,
        overhead_cost=overhead_cost,
        unpriced_calls=unpriced,
    )


def _max_gap(recs: Sequence[UsageRecord]) -> int | None:
    """Longest idle gap between consecutive calls -- only derivable when the
    store records each call (Claude Code), not per-model totals."""
    if any(r.calls != 1 for r in recs) or len(recs) < 2:
        return None
    times = sorted(r.first_ts for r in recs)
    return int(max((b - a).total_seconds() for a, b in zip(times, times[1:], strict=False)))


@dataclass(frozen=True)
class CostGroup:
    key: str
    sessions: int
    calls: int
    est_cost: float | None
    unpriced_calls: int = 0


def _group_key(r: UsageRecord, by: str) -> str:
    if by == "folder":
        return folder_of(r.cwd)
    if by == "model":
        return normalise_model(r.model) if r.model else "(unknown)"
    if by == "store":
        return r.source.value
    return r.last_ts.date().isoformat()  # "day"


def group_costs(records: Iterable[UsageRecord], by: str, cost: CostConfig) -> list[CostGroup]:
    """Spend grouped by folder, model, store or day. Built from records, not
    sessions, so a mixed-model session splits across models and every
    grouping sums to the same total as --by session."""
    if by not in GROUP_KEYS or by == "session":
        raise ValueError(f"group_costs: by must be one of {GROUP_KEYS[1:]}")
    acc: dict[str, dict] = defaultdict(
        lambda: {"sessions": set(), "calls": 0, "cost": 0.0, "priced": False, "unpriced": 0}
    )
    for r in records:
        g = acc[_group_key(r, by)]
        g["sessions"].add((r.source, r.session_id))
        g["calls"] += r.calls
        c = record_cost(r, cost)
        if c is None:
            g["unpriced"] += r.calls
        else:
            g["priced"] = True
            g["cost"] += c
    groups = [
        CostGroup(
            key=k,
            sessions=len(g["sessions"]),
            calls=g["calls"],
            est_cost=g["cost"] if g["priced"] else None,
            unpriced_calls=g["unpriced"],
        )
        for k, g in acc.items()
    ]
    if by == "day":
        groups.sort(key=lambda g: g.key)
    else:
        groups.sort(key=lambda g: (-(g.est_cost or 0.0), -g.calls, g.key))
    return groups


@dataclass(frozen=True)
class Lever:
    """The single biggest cost lever, chosen by a fixed rule (largest share)."""

    kind: str  # "top_tier_model" | "long_sessions" | "none"
    share: float  # 0.0-1.0 of estimated spend
    text: str


@dataclass(frozen=True)
class CostSummary:
    since: datetime | None
    sessions: tuple[SessionUsage, ...]
    total: float  # priced spend only
    overhead: float
    unpriced_models: dict[str, int]  # normalised model -> calls with no price
    no_usage: dict[str, int]  # source -> sessions with prompts but no usage data
    large_sessions: int  # over cost.context_warn_tokens per call
    large_share: float
    top_tier_share: float  # Opus/Fable share of spend
    lever: Lever
    cost: CostConfig = field(repr=False, default_factory=CostConfig)


_TOP_TIER = ("claude-opus", "claude-fable", "claude-mythos")


def summarise(
    records: Sequence[UsageRecord],
    cost: CostConfig,
    *,
    since: datetime | None = None,
    prompt_sessions: dict[str, set[str]] | None = None,
) -> CostSummary:
    sessions = aggregate_sessions(records, cost)
    total = sum(s.est_cost or 0.0 for s in sessions)
    unpriced: Counter[str] = Counter()
    top_tier = 0.0
    for r in records:
        c = record_cost(r, cost)
        if c is None:
            unpriced[normalise_model(r.model) if r.model else "(unknown)"] += r.calls
        elif r.model and normalise_model(r.model).startswith(_TOP_TIER):
            top_tier += c
    large = [s for s in sessions if s.context_per_call > cost.context_warn_tokens]
    large_cost = sum(s.est_cost or 0.0 for s in large)
    usage_ids = {(s.source.value, s.session_id) for s in sessions}
    no_usage = {
        source: n
        for source, ids in sorted((prompt_sessions or {}).items())
        if (n := sum(1 for sid in ids if (source, sid) not in usage_ids))
    }
    top_share = top_tier / total if total else 0.0
    large_share = large_cost / total if total else 0.0
    return CostSummary(
        since=since,
        sessions=tuple(sessions),
        total=total,
        overhead=sum(s.overhead_cost for s in sessions),
        unpriced_models=dict(unpriced.most_common()),
        no_usage=no_usage,
        large_sessions=len(large),
        large_share=large_share,
        top_tier_share=top_share,
        lever=_lever(top_share, len(large), large_share, cost),
        cost=cost,
    )


def _lever(top_share: float, n_large: int, large_share: float, cost: CostConfig) -> Lever:
    """Largest share wins; a tie goes to long sessions (the fix is free:
    starting a new session costs nothing, switching model may cost quality)."""
    ctx = f"{cost.context_warn_tokens // 1000}k"
    if large_share == 0 and top_share == 0:
        return Lever("none", 0.0, "No single lever stands out.")
    if large_share >= top_share:
        noun = "session" if n_large == 1 else "sessions"
        return Lever(
            "long_sessions",
            large_share,
            f"{n_large} {noun} over {ctx} context per call carry {large_share:.0%} of est. "
            "spend. Split long sessions at topic changes and start fresh.",
        )
    return Lever(
        "top_tier_model",
        top_share,
        f"Opus/Fable models carry {top_share:.0%} of est. spend. Try a Sonnet-tier model "
        "for routine work (model fit flags the overpowered prompts).",
    )
