"""The cost block shared by `report`, `dash` (terminal and HTML) and `cost`:
totals, the A14-A16 scorecard, weekly spend and the lever line."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from prompt_coach.analysis.cost import CostSummary, record_cost, summarise
from prompt_coach.analysis.session_rules import (
    SessionKey,
    SessionRuleScore,
    SessionRuleSummary,
    score_sessions,
    summarise_rules,
)
from prompt_coach.config import CostConfig
from prompt_coach.models import Prompt, UsageRecord


@dataclass(frozen=True)
class CostSection:
    summary: CostSummary
    rules: tuple[SessionRuleSummary, ...]
    scores: dict[SessionKey, dict[str, SessionRuleScore]]
    weekly: tuple[float, ...]  # estimated spend per week, oldest first

    @property
    def has_usage(self) -> bool:
        return bool(self.summary.sessions)

    @property
    def longest(self):
        """The session with the most calls (the report's "longest session")."""
        return max(self.summary.sessions, key=lambda s: s.api_calls, default=None)


def weekly_spend(
    records: Sequence[UsageRecord], cost: CostConfig, weeks: int = 12, now: datetime | None = None
) -> tuple[float, ...]:
    now = now or datetime.now(tz=UTC)
    start = now - timedelta(weeks=weeks)
    buckets = [0.0] * weeks
    for r in records:
        if r.last_ts < start:
            continue
        c = record_cost(r, cost)
        if c is not None:
            buckets[min(weeks - 1, int((r.last_ts - start).days // 7))] += c
    return tuple(buckets)


def build_cost_section(
    records: Sequence[UsageRecord],
    prompts: Sequence[Prompt],
    cost: CostConfig,
    *,
    since: datetime | None = None,
    prompt_sessions: dict[str, set[str]] | None = None,
    weeks: int = 12,
) -> CostSection:
    summary = summarise(records, cost, since=since, prompt_sessions=prompt_sessions)
    scores = score_sessions(summary.sessions, records, prompts, cost)
    weights = {(s.source.value, s.session_id): s.est_cost or 0.0 for s in summary.sessions}
    return CostSection(
        summary=summary,
        rules=summarise_rules(scores, weights),
        scores=scores,
        weekly=weekly_spend(records, cost, weeks=weeks),
    )
