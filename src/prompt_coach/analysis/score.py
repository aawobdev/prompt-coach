"""Deterministic prompt scoring: 0-100 based on structure, clarity, and scope.

Pure functions, no I/O, no LLM. Every factor is a configurable weight in
SCORE_WEIGHTS so the algorithm can be tuned without code changes.

Score breakdown:
  - Base: 50
  - + bonuses for good practices (format, examples, constraints, grounding)
  - - penalties for bad practices (no success criteria, vague scope,
    rambling, conflated asks, bare pastes)
  - Clamped to [0, 100]

A follow-up-message exemption (skip the format/example/constraints
penalties when the text looked like a short conversational continuation)
was tried and removed here, not merged (code review 2026-09-24): matching
on the first word alone ("yes"/"now"/"check"/"try"/"and"/...) fired on
long, fully-formed prompts too -- "Now build X with these constraints:
must be stdlib only..." starts with "Now" and would have had its own
constraints/example penalties silently waived. The penalty it would gate
is now the single flat no-success-criteria one (see _factors), but the
problem stands: "is this really just a follow-up" isn't answerable from
the isolated string. score_prompt takes no session
context (it's called with a bare string from streaks.py and in a
per-prompt corpus loop from rubric.py), so it has no sound way to tell a
genuine continuation from a fresh, unstructured ask at that length --
nudge.py's gather_context/_tail_human_prompts already reconstructs real
session continuity from the transcript for exactly this reason, and that
is the layer this belongs at if it's rebuilt.
"""

from __future__ import annotations

import re

from prompt_coach.analysis.metrics import (
    authored_text,
    has_constraints,
    has_example,
    has_sections,
    has_structured_output,
    is_bare_paste,
)

_BROAD_SCOPE = re.compile(r"\b(everything|all of|whole|revamp)\b", re.IGNORECASE)
_MULTIPLE_ASKS = re.compile(
    r"(?:and\s+also|as\s+well\s+as|in\s+addition|separately|"
    r"firstly|secondly|lastly|next\s*[,;.])",
    re.IGNORECASE,
)
_PROJECT_REF = re.compile(
    r"\b(src[/\\\\]|test[/\\\\]|lib[/\\\\]|app[/\\\\]|config|docker|"
    r"dockerfile|requirements|package\.json|setup\.py|Cargo\.toml|go\.mod)\b",
    re.IGNORECASE,
)

SCORE_WEIGHTS = {
    # Bonuses (positive)
    "has_output_format": 15,
    "has_example": 10,
    "has_constraints": 10,
    "has_project_context": 10,
    "has_sections": 5,
    "enough_detail": 5,
    # Penalties (negative)
    "too_short": -10,
    "no_success_criteria": -10,
    "vague_scope": -15,
    "too_long_rambling": -10,
    "multiple_asks": -10,
    "bare_paste": -15,
}

# Length rules read the authored text: a pasted log is neither detail nor
# rambling, and a bare paste's leftover banner line isn't detail either.
# too_short reads the whole prompt, so a paste with a one-word ask is
# judged by bare_paste alone, not penalised twice.
_DETAIL_CHARS = 50
_SHORT_CHARS = 20
_RAMBLING_CHARS = 500


def _factors(text: str) -> tuple[list[tuple[str, int]], list[tuple[str, int]]]:
    """(bonuses, penalties) as (label, weight) pairs: the one rule list.

    Missing format/example/constraints used to be three penalties, each
    switched on only above a length (100/150/200 chars), on top of the
    bonus the prompt already didn't earn. Writing more cost up to 35
    points, so a careful 400-char brief scored below "look at my inbox"
    (real data 2026-09-25: 200-500 char prompts median 20, 20-80 char
    median 50). Now a missing signal only costs its unearned bonus, plus
    one flat penalty, at any length, when there is no success criterion
    at all. At equal signals, score never falls as a prompt gets longer,
    until the rambling cap.
    """
    authored = len(authored_text(text))
    bare_paste = is_bare_paste(text)
    fmt = has_structured_output(text)
    example = has_example(text)
    constraints = has_constraints(text)
    w = SCORE_WEIGHTS
    bonuses = []
    penalties = []

    if fmt:
        bonuses.append(("Output format specified", w["has_output_format"]))
    if example:
        bonuses.append(("Example provided", w["has_example"]))
    if constraints:
        bonuses.append(("Constraints/boundaries set", w["has_constraints"]))
    if _PROJECT_REF.search(text):
        bonuses.append(("Project context referenced", w["has_project_context"]))
    if has_sections(text):
        bonuses.append(("Section headers structure prompt", w["has_sections"]))
    if authored >= _DETAIL_CHARS and not bare_paste:
        bonuses.append((f"Enough detail ({_DETAIL_CHARS}+ chars)", w["enough_detail"]))

    if len(text) < _SHORT_CHARS:
        penalties.append((f"Too short (< {_SHORT_CHARS} chars)", w["too_short"]))
    elif not (fmt or example or constraints):
        penalties.append(
            ("No success criteria (format, example or constraint)", w["no_success_criteria"])
        )
    if _BROAD_SCOPE.search(text) and not constraints:
        penalties.append(("Vague/broad scope", w["vague_scope"]))
    if authored > _RAMBLING_CHARS and not fmt:
        penalties.append(
            (f"Rambling (> {_RAMBLING_CHARS} chars, no format)", w["too_long_rambling"])
        )
    if _MULTIPLE_ASKS.search(text):
        penalties.append(("Multiple conflated asks", w["multiple_asks"]))
    if bare_paste:
        penalties.append(("Mostly pasted, little instruction", w["bare_paste"]))
    return bonuses, penalties


def score_prompt(text: str) -> int:
    """Score a prompt from 0-100 based on deterministic quality signals."""
    bonuses, penalties = _factors(text)
    return max(0, min(100, 50 + sum(v for _, v in bonuses) + sum(v for _, v in penalties)))


def score_breakdown(text: str) -> dict:
    """Return a detailed breakdown of the scoring factors."""
    bonuses, penalties = _factors(text)
    return {
        "score": score_prompt(text),
        "bonuses": bonuses,
        "penalties": penalties,
    }
