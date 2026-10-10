"""Session-level cost rules A14-A16 (deterministic, no LLM).

A1-A13 score single prompts (analysis/rubric.py); these score whole
sessions, so they live on their own path and leave prompt scoring alone.
1.0 is good, 0.0 weak, None not applicable -- the same bands as score_band.
A12 (cache-friendly prefix) stays "not observable"; these don't replace it.
"""

from __future__ import annotations

import bisect
import dataclasses
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from prompt_coach.analysis.model_fit import detect_mismatches
from prompt_coach.config import CostConfig
from prompt_coach.models import Prompt, SessionUsage, UsageRecord

SESSION_RULES = {
    "A14": "Right-sized model",
    "A15": "Session size",
    "A16": "No cold resume",
}

SessionKey = tuple[str, str]  # (source, session_id)


@dataclass(frozen=True)
class SessionRuleScore:
    rule: str
    score: float | None
    note: str = ""  # counts and model names only, never prompt text


@dataclass(frozen=True)
class SessionRuleSummary:
    rule: str
    title: str
    mean: float | None  # weighted by estimated spend (see summarise_rules)
    coverage: int  # sessions actually scored


def a15_session_size(s: SessionUsage, cost: CostConfig) -> SessionRuleScore:
    """1.0 within both limits, 0.5 over one, 0.0 over both. Every call
    resends the context, so context per call x calls is the cost driver."""
    big_context = s.context_per_call > cost.context_warn_tokens
    many_calls = s.api_calls > cost.calls_warn
    score = 1.0 - 0.5 * (big_context + many_calls)
    note = f"{s.api_calls} calls, ~{s.context_per_call / 1000:.0f}k context per call"
    return SessionRuleScore("A15", score, note)


def a16_cold_resume(records: Sequence[UsageRecord], cost: CostConfig) -> SessionRuleScore:
    """Share of calls that came after an idle gap longer than the cache lifetime
    (`idle_minutes`, ~5 min) while carrying a large context: each one rewrites
    the whole context at the cache-write rate. n/a unless the store records
    each call (per-model totals have no gaps)."""
    if not records or any(r.calls != 1 for r in records):
        return SessionRuleScore("A16", None, "gaps not recorded by this store")
    ordered = sorted(records, key=lambda r: r.first_ts)
    idle = cost.idle_minutes * 60
    cold = sum(
        1
        for prev, cur in zip(ordered, ordered[1:], strict=False)
        if (cur.first_ts - prev.first_ts).total_seconds() > idle
        and cur.context_tokens >= cost.context_warn_tokens
    )
    return SessionRuleScore(
        "A16", 1.0 - cold / len(ordered), f"{cold} of {len(ordered)} calls after an idle gap"
    )


def a14_right_sized(prompts: Sequence[Prompt]) -> SessionRuleScore:
    """Model fit's tiers, per session: the share of classifiable prompts that
    were not overpowered (an underpowered model costs less, so it counts as a
    fit here). n/a when no prompt had a classifiable model."""
    fit = detect_mismatches(prompts, "descriptive")
    if not fit.coverage:
        return SessionRuleScore("A14", None, "no classifiable model")
    over = [f for f in fit.findings if f.direction == "overpowered"]
    if not over:
        return SessionRuleScore("A14", 1.0, f"{fit.coverage} prompts fit their model")
    model = max({f.model for f in over}, key=lambda m: sum(f.model == m for f in over))
    return SessionRuleScore(
        "A14",
        1.0 - len(over) / fit.coverage,
        f"{len(over)} of {fit.coverage} prompts overpowered for {model}",
    )


def _with_models(prompts: list[Prompt], records: list[UsageRecord]) -> list[Prompt]:
    """Fill a prompt's missing model from usage: the first call at or after it
    is the reply. The prompt reader links few turns to their reply in current
    Claude Code transcripts; usage records always name the model."""
    calls = sorted((r for r in records if r.model), key=lambda r: r.first_ts)
    if not calls:
        return prompts
    times = [r.first_ts for r in calls]
    out = []
    for p in prompts:
        if p.model is None:
            i = min(bisect.bisect_left(times, p.timestamp), len(calls) - 1)
            p = dataclasses.replace(p, model=calls[i].model)
        out.append(p)
    return out


def score_sessions(
    sessions: Iterable[SessionUsage],
    records: Iterable[UsageRecord],
    prompts: Iterable[Prompt],
    cost: CostConfig,
) -> dict[SessionKey, dict[str, SessionRuleScore]]:
    recs_by: dict[SessionKey, list[UsageRecord]] = defaultdict(list)
    for r in records:
        recs_by[(r.source.value, r.session_id)].append(r)
    prompts_by: dict[SessionKey, list[Prompt]] = defaultdict(list)
    for p in prompts:
        prompts_by[(p.source.value, p.session_id)].append(p)
    out: dict[SessionKey, dict[str, SessionRuleScore]] = {}
    for s in sessions:
        key = (s.source.value, s.session_id)
        recs = recs_by.get(key, [])
        out[key] = {
            "A14": a14_right_sized(_with_models(prompts_by.get(key, []), recs)),
            "A15": a15_session_size(s, cost),
            "A16": a16_cold_resume(recs, cost),
        }
    return out


def summarise_rules(
    scores: dict[SessionKey, dict[str, SessionRuleScore]],
    weights: dict[SessionKey, float] | None = None,
) -> tuple[SessionRuleSummary, ...]:
    """Mean over the sessions each rule could score, weighted by estimated
    spend: these are cost rules, and an unweighted mean let 1,400 tiny
    sessions rate session size "good" while 97% of spend sat in oversized
    ones (dev data, 2026-10-10). Falls back to unweighted when nothing in
    the scored set has a price."""
    out = []
    for rule, title in SESSION_RULES.items():
        scored = [(k, v[rule].score) for k, v in scores.items() if v[rule].score is not None]
        w = [(weights or {}).get(k, 0.0) for k, _ in scored]
        if not sum(w):
            w = [1.0] * len(scored)
        total = sum(w)
        mean = (
            sum(wi * sc for wi, (_, sc) in zip(w, scored, strict=True)) / total if scored else None
        )
        out.append(SessionRuleSummary(rule=rule, title=title, mean=mean, coverage=len(scored)))
    return tuple(out)
