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

# Signals a prompt includes a worked example. "e.g." and "example:" sit
# outside the \b-wrapped group deliberately (code review 2026-09-24): a
# trailing \b right after either can never fire, since both end in a
# non-word character (".", ":") and whatever follows (a space, a closing
# paren, end of string) is non-word too, so no word/non-word transition
# exists there -- confirmed both were dead for realistic prose ("e.g. a
# short json blob", "example: baz") on `main`, before this diff touched
# either, with zero test coverage catching it either way. A prior fix
# tried `e\.g\.(?=\s)` inside the \b-wrapped group and stayed just as
# dead. `(?!\w)` fixes both: matches "e.g. ", "(e.g.)", "example: X" and
# end-of-string, and only excludes the case that's arguably not real
# usage anyway (e.g.foo, example:foo glued with no space).
_EXAMPLE = re.compile(
    r"\b(for example|for instance|like this|such as)\b" r"|\be\.g\.(?!\w)|\bexample:(?!\w)|==|```",
    re.IGNORECASE,
)
# Signals explicit constraints/limits.
_CONSTRAINT = re.compile(
    r"\b(must|only|never|always|constraint|require[sd]?|max|min|limit|exactly"
    r"|no more than|at least|stdlib only|do not|don't)\b",
    re.IGNORECASE,
)
# A bullet or numbered list item, marker only -- shared with has_sections
# below and with rubric.py's A4 structure check (code review 2026-09-24:
# this exact "is it a list item" pattern existed independently in three
# places -- here, rubric.py, and docs.py -- with different marker sets;
# this is now the one metrics.py/rubric.py both build on).
LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
# Implicit constraints from bullet lists and numbered criteria -- but only
# when the item itself states a bound (a number with a unit or a
# comparison, or an explicit limit word), not just "is formatted as a
# list". A prior version matched ANY bullet/numbered line regardless of
# content (code review 2026-09-24: "- resized down\n- stopped\n- migrated"
# -- a plain list of options, no limits at all -- registered as "has
# constraints", which defeated score.py's vague_scope penalty for
# genuinely unconstrained "revamp everything" + bullet-list prompts).
# Real constraint-bearing bullets from this repo's own test fixtures still
# match: "CPU/RAM usage < 30% for 7 days", "idle for > 30 days".
_IMPLICIT_CONSTRAINT = re.compile(
    LIST_ITEM.pattern + r".*(?:\d+\s*%|\d+\s*(?:days?|hours?|minutes?|weeks?|months?|years?)\b"
    r"|[<>]=?\s*\d|\d\s*[<>]=?|\bwithin\b|\bat least\b|\bno more than\b)",
    re.IGNORECASE | re.MULTILINE,
)
# A markdown heading or a short "Label:" line -- general structure signal,
# shared with rubric.py's A4 check the same way LIST_ITEM is (code review
# 2026-09-24: has_sections below used to hardcode a closed 10-word English
# list -- Problem/Expected/Steps/Goal/... -- that couldn't recognize
# "Root Cause:"/"Fix:" or any team's own vocabulary; this generic pattern,
# already proven in rubric.py's A4 scoring, has no such ceiling).
SECTION_LABEL = re.compile(r"^\s*(?:#{1,4}\s|\w[\w /]{0,30}:\s)", re.MULTILINE)
# Signals a requested output shape. `table`/`columns`/`tabular` bare words
# were removed here (code review 2026-09-24): they matched any mention of
# a table/column, e.g. "the users table has a race condition" or "the CSS
# layout columns", not a request for tabular output. The narrower
# phrases below (`markdown table`, `table with`, `as a table`, and
# `return a table`/`return as table` via the `return ...` alternative)
# cover the real "wants tabular output" cases without that blast radius.
_STRUCTURED = re.compile(
    r"\b(json|yaml|csv|markdown table|table with|as a table|bullet(?:ed)? list"
    r"|numbered list|list\s+(?:of|showing|with|containing)|schema|output format|format:"
    r"|return\s+(?:only|a|as)\s+(?:json|yaml|csv|table|checklist|list|summary)"
    r"|respond with only"
    r"|report\s+(?:back\s+)?(?:pass|fail|pass/fail|count|result)s?\b)\b",
    re.IGNORECASE,
)


def estimate_tokens(text: str) -> float:
    return len(text) / 4.0


def has_example(text: str) -> bool:
    return bool(_EXAMPLE.search(text))


def has_constraints(text: str) -> bool:
    return bool(_CONSTRAINT.search(text)) or bool(_IMPLICIT_CONSTRAINT.search(text))


def has_structured_output(text: str) -> bool:
    return bool(_STRUCTURED.search(text))


def has_sections(text: str) -> bool:
    """2+ heading/labeled-line markers = a prompt organised into sections.
    Deliberately excludes bare list items (LIST_ITEM): a prompt with two
    plain bullets isn't "sectioned" in the sense this bonus rewards, and
    conflating the two would double-count the same structure signal
    has_constraints' implicit-list branch already credits (code review
    2026-09-24)."""
    matches = SECTION_LABEL.findall(text)
    return len(matches) >= 2


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
