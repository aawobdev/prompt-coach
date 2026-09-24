"""Deterministic prompt scoring: 0-100 based on structure, clarity, and scope.

Pure functions, no I/O, no LLM. Every factor is a configurable weight in
SCORE_WEIGHTS so the algorithm can be tuned without code changes.

Score breakdown:
  - Base: 50
  - + bonuses for good practices (format, examples, constraints, grounding)
  - - penalties for bad practices (vague scope, missing structure, conflated asks)
  - Clamped to [0, 100]

A follow-up-message exemption (skip the format/example/constraints
penalties when the text looked like a short conversational continuation)
was tried and removed here, not merged (code review 2026-09-24): matching
on the first word alone ("yes"/"now"/"check"/"try"/"and"/...) fired on
long, fully-formed prompts too -- "Now build X with these constraints:
must be stdlib only..." starts with "Now" and would have had its own
constraints/example penalties silently waived. A length cap doesn't save
it either: the three penalties this would gate only apply at >=100/150/200
chars, exactly the range where "is this really just a follow-up" stops
being answerable from the isolated string. score_prompt takes no session
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
    has_constraints,
    has_example,
    has_sections,
    has_structured_output,
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
    "sweet_spot_length": 5,
    # Penalties (negative)
    "too_short": -10,
    "no_output_format": -15,
    "no_example": -10,
    "no_constraints": -10,
    "vague_scope": -15,
    "too_long_rambling": -10,
    "multiple_asks": -10,
}


def score_prompt(text: str) -> int:
    """Score a prompt from 0-100 based on deterministic quality signals."""
    score = 50
    length = len(text)

    # -- Bonuses --
    if has_structured_output(text):
        score += SCORE_WEIGHTS["has_output_format"]
    if has_example(text):
        score += SCORE_WEIGHTS["has_example"]
    if has_constraints(text):
        score += SCORE_WEIGHTS["has_constraints"]
    if _PROJECT_REF.search(text):
        score += SCORE_WEIGHTS["has_project_context"]
    if has_sections(text):
        score += SCORE_WEIGHTS["has_sections"]
    if 50 <= length <= 300:
        score += SCORE_WEIGHTS["sweet_spot_length"]

    # -- Penalties --
    if length < 20:
        score += SCORE_WEIGHTS["too_short"]
    if not has_structured_output(text) and length >= 200:
        score += SCORE_WEIGHTS["no_output_format"]
    if not has_example(text) and length >= 100:
        score += SCORE_WEIGHTS["no_example"]
    if not has_constraints(text) and length >= 150:
        score += SCORE_WEIGHTS["no_constraints"]
    if _BROAD_SCOPE.search(text) and not has_constraints(text):
        score += SCORE_WEIGHTS["vague_scope"]
    if length > 500 and not has_structured_output(text):
        score += SCORE_WEIGHTS["too_long_rambling"]
    if _MULTIPLE_ASKS.search(text):
        score += SCORE_WEIGHTS["multiple_asks"]

    return max(0, min(100, score))


def score_breakdown(text: str) -> dict:
    """Return a detailed breakdown of the scoring factors."""
    length = len(text)
    bonuses = []
    penalties = []

    if has_structured_output(text):
        bonuses.append(("Output format specified", SCORE_WEIGHTS["has_output_format"]))
    if has_example(text):
        bonuses.append(("Example provided", SCORE_WEIGHTS["has_example"]))
    if has_constraints(text):
        bonuses.append(("Constraints/boundaries set", SCORE_WEIGHTS["has_constraints"]))
    if _PROJECT_REF.search(text):
        bonuses.append(("Project context referenced", SCORE_WEIGHTS["has_project_context"]))
    if has_sections(text):
        bonuses.append(("Section headers structure prompt", SCORE_WEIGHTS["has_sections"]))
    if 50 <= length <= 300:
        bonuses.append(("Concise length (50-300 chars)", SCORE_WEIGHTS["sweet_spot_length"]))

    if length < 20:
        penalties.append(("Too short (< 20 chars)", SCORE_WEIGHTS["too_short"]))
    if not has_structured_output(text) and length >= 200:
        penalties.append(("Missing output format", SCORE_WEIGHTS["no_output_format"]))
    if not has_example(text) and length >= 100:
        penalties.append(("No example provided", SCORE_WEIGHTS["no_example"]))
    if not has_constraints(text) and length >= 150:
        penalties.append(("No constraints/boundaries", SCORE_WEIGHTS["no_constraints"]))
    if _BROAD_SCOPE.search(text) and not has_constraints(text):
        penalties.append(("Vague/broad scope", SCORE_WEIGHTS["vague_scope"]))
    if length > 500 and not has_structured_output(text):
        penalties.append(("Rambling (> 500 chars, no format)", SCORE_WEIGHTS["too_long_rambling"]))
    if _MULTIPLE_ASKS.search(text):
        penalties.append(("Multiple conflated asks", SCORE_WEIGHTS["multiple_asks"]))

    return {
        "score": score_prompt(text),
        "bonuses": bonuses,
        "penalties": penalties,
    }
