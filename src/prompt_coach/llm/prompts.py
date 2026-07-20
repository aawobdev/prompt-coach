"""Versioned prompt templates for the analysis pipeline.

Layout follows prompting-standards A11: instruction first, fenced data in the
middle, output contract restated at the end with a worked example. Version
strings feed the llm_cache key, so bumping a version naturally invalidates
cached results for that template.
"""

from __future__ import annotations

RUBRIC_JUDGE_VERSION = "rubric-judge.v1"
PATTERN_MAP_VERSION = "pattern-map.v1"
PATTERN_REDUCE_VERSION = "pattern-reduce.v1"
QUERY_ANSWER_VERSION = "query-answer.v1"

SYSTEM_ANALYST = (
    "You are a prompt-coaching analyst. You judge how well USER-WRITTEN prompts"
    " to LLMs follow prompt-engineering best practice. Judge only from the text"
    " provided; if something cannot be determined from it, say so via the score"
    " rather than inventing context. Respond with ONLY a JSON object."
)

RUBRIC_JUDGE = """Score each numbered prompt below against these rules, 0.0 (absent) to 1.0 (exemplary):

- A1 explicit: states exactly what is wanted, with names, paths, counts, or limits.
- A2 positive: leads with what to do rather than only prohibitions.
- A3 why: gives rationale so the executor can make sensible micro-decisions.
- A8 grounded: sticks to stated facts and licenses "I don't know"; no invitation to invent.

Prompts to judge:
<prompts>
{prompts_block}
</prompts>

Output contract: ONLY a JSON object mapping prompt number to rule scores, like:
{{"1": {{"A1": 0.8, "A2": 1.0, "A3": 0.2, "A8": 0.5}}, "2": {{"A1": 0.1, "A2": 0.6, "A3": 0.0, "A8": 0.3}}}}
Every prompt number present, every rule present, scores between 0.0 and 1.0. No prose."""

PATTERN_MAP = """Analyse this batch of prompts from one user's LLM history. Identify, strictly from the text:

1. topics: short domain labels with rough share of the batch (e.g. "coding/python", "homelab/ops", "writing/email").
2. habits: recurring stylistic behaviours, good or bad, phrased specifically.
3. weaknesses: patterns likely to produce poor results, each with the number of one prompt showing it.
4. strengths: things done consistently well, each with the number of one prompt showing it.

Base every claim on the prompts shown; do not generalise beyond them.

<prompts>
{prompts_block}
</prompts>

Output contract: ONLY JSON like:
{{"topics": [{{"topic": "coding/python", "share": 0.4}}], "habits": ["often pastes error text without saying what was already tried"], "weaknesses": [{{"pattern": "asks multiple unrelated things in one prompt", "example_prompt": 3}}], "strengths": [{{"pattern": "states the exact output format wanted", "example_prompt": 1}}]}}"""

PATTERN_REDUCE = """You are given digests of several batches of one user's prompt history (produced by a previous analysis pass). Merge them into a single coaching view.

Rules: merge duplicates, keep only patterns supported by more than one batch where possible, be specific (name the behaviour, not "be clearer"), and never invent a pattern no digest mentions. Topic shares: combine into one distribution summing to about 1.0.

<digests>
{digests_block}
</digests>

Output contract: ONLY JSON like:
{{"strengths": ["states exact output format", "provides file paths and versions"], "growth_areas": ["bundles unrelated asks into one prompt", "rarely explains why constraints matter"], "notable_patterns": ["iterates in short refinement chains rather than restating"], "topics": [{{"topic": "coding", "share": 0.5}}, {{"topic": "homelab/ops", "share": 0.3}}]}}
Three to five items per list, most important first."""

QUERY_ANSWER = """Answer the user's question about their own prompt history using ONLY the excerpts below. Cite the excerpts you used by their bracketed reference (e.g. [2]). If the excerpts cannot answer the question, say exactly that; do not guess.

Question: {question}

<excerpts>
{excerpts_block}
</excerpts>

Output contract: ONLY JSON like:
{{"answer": "Last week you mostly worked on the crawford-measure tax tool [1][3] and homelab DNS issues [2].", "used_refs": [1, 2, 3], "confident": true}}
Set "confident" to false when the excerpts only partially cover the question."""


def format_prompts_block(prompts: list[str], truncate: int = 1500) -> str:
    """Numbered, fenced prompt list for the judge/map templates."""
    lines = []
    for i, content in enumerate(prompts, 1):
        text = content[:truncate] + ("..." if len(content) > truncate else "")
        lines.append(f'{i}. """{text}"""')
    return "\n\n".join(lines)


def format_excerpts_block(excerpts: list[tuple[str, str]], truncate: int = 800) -> str:
    """[n] (ref) text lines for the query template. excerpts = [(ref, content)]."""
    lines = []
    for i, (ref, content) in enumerate(excerpts, 1):
        text = content[:truncate] + ("..." if len(content) > truncate else "")
        lines.append(f"[{i}] ({ref}) {text}")
    return "\n\n".join(lines)
