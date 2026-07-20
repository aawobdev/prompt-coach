"""Deterministic style metrics. Pure functions, no I/O, no LLM.

These run over the FULL corpus (cheap) and are exact and repeatable: they are
the stable baseline that phase-2 trend analysis will diff against, so keep
every heuristic here deterministic and versionable.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence

from prompt_coach.models import Prompt, PromptOrigin, SegmentMetrics, StyleMetrics

# Signals a prompt includes a worked example.
_EXAMPLE = re.compile(
    r"\b(e\.g\.|for example|for instance|example:|like this|such as)\b|==|```", re.IGNORECASE
)
# Signals explicit constraints/limits.
_CONSTRAINT = re.compile(
    r"\b(must|only|never|always|constraint|require[sd]?|max|min|limit|exactly"
    r"|no more than|at least|stdlib only|do not|don't)\b",
    re.IGNORECASE,
)
# Signals a requested output shape.
_STRUCTURED = re.compile(
    r"\b(json|yaml|csv|markdown table|table with|as a table|bullet(?:ed)? list"
    r"|numbered list|schema|output format|format:|return only|respond with only)\b",
    re.IGNORECASE,
)


def estimate_tokens(text: str) -> float:
    return len(text) / 4.0


def has_example(text: str) -> bool:
    return bool(_EXAMPLE.search(text))


def has_constraints(text: str) -> bool:
    return bool(_CONSTRAINT.search(text))


def has_structured_output(text: str) -> bool:
    return bool(_STRUCTURED.search(text))


def _segment(prompts: Sequence[Prompt]) -> SegmentMetrics | None:
    if not prompts:
        return None
    token_counts = [estimate_tokens(p.content) for p in prompts]

    sessions: dict[str, list[Prompt]] = {}
    for p in prompts:
        sessions.setdefault(f"{p.source}:{p.session_id}", []).append(p)
    for sess in sessions.values():
        sess.sort(key=lambda p: p.timestamp)

    multi = sum(1 for sess in sessions.values() if len(sess) > 1)
    first_ratios = [
        len(sess[0].content) / total
        for sess in sessions.values()
        if (total := sum(len(p.content) for p in sess)) > 0
    ]

    n = len(prompts)
    return SegmentMetrics(
        prompt_count=n,
        session_count=len(sessions),
        avg_prompt_tokens=sum(token_counts) / n,
        median_prompt_tokens=statistics.median(token_counts),
        prompts_per_session=n / len(sessions),
        refinement_rate=multi / len(sessions),
        example_rate=sum(1 for p in prompts if has_example(p.content)) / n,
        constraint_rate=sum(1 for p in prompts if has_constraints(p.content)) / n,
        structured_output_rate=sum(1 for p in prompts if has_structured_output(p.content)) / n,
        avg_first_message_ratio=(sum(first_ratios) / len(first_ratios) if first_ratios else 0.0),
    )


def compute_metrics(prompts: Sequence[Prompt]) -> StyleMetrics:
    return StyleMetrics(
        human=_segment([p for p in prompts if p.origin is PromptOrigin.HUMAN]),
        machine=_segment([p for p in prompts if p.origin is PromptOrigin.MACHINE]),
        overall=_segment(list(prompts)),
    )
