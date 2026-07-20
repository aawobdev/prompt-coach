"""Scoring against the prompting-standards rubric (A1-A13).

Two judges:
- deterministic: structural rules checkable from text, run over the whole corpus
- llm: judgement rules (A1/A2/A3/A8), run on a seeded stratified sample so
  results are comparable between runs and affordable on a local model

Applicability is explicit. Orchestration-only rules (A7/A9/A10/A13) are
checked deterministically on MACHINE prompts (a hermes -z task spec should
carry Verify/Reasoning/Sampling fields) and are N/A for hand-typed chat.
A12 (prompt caching) is not observable from a single prompt's text at all,
so it is never scored; faking a heuristic would be noise dressed as signal.
"""

from __future__ import annotations

import hashlib
import random
import re
from collections.abc import Sequence

from prompt_coach.analysis.metrics import (
    has_constraints,
    has_example,
    has_structured_output,
)
from prompt_coach.cache import CacheDB
from prompt_coach.llm import prompts as tpl
from prompt_coach.llm.client import LLMUnavailable, LocalLLM
from prompt_coach.models import (
    Prompt,
    PromptOrigin,
    RubricSummary,
    RuleScore,
    RuleSummary,
)

RUBRIC_VERSION = "a13.v1"

RULE_TITLES = {
    "A1": "Explicit and specific",
    "A2": "Positive instructions",
    "A3": "Gives the why",
    "A4": "Structured prompt",
    "A5": "Output contract",
    "A6": "Worked examples",
    "A7": "Reasoning directive",
    "A8": "Grounded, licenses unknowns",
    "A9": "Atomic and verifiable",
    "A10": "Self-verification",
    "A11": "Long-context placement",
    "A12": "Cache-friendly prefix",
    "A13": "Sampling hint",
}

LLM_RULES = ("A1", "A2", "A3", "A8")
_ORCHESTRATION_RULES = ("A7", "A9", "A10", "A13")

_HEADING_OR_LIST = re.compile(r"^\s*(#{1,4}\s|\d+[.)]\s|[-*]\s|\w[\w /]{0,30}:\s)", re.MULTILINE)
_FENCED_OR_TAGGED = re.compile(r"```|<[a-z_]+>")
_VERIFY = re.compile(r"\b(verify|check that|confirm|acceptance|test with)\b[:\s]", re.IGNORECASE)
_SELF_CHECK = re.compile(
    r"\b(re-read|double-check|self-check|before reporting|after writing)\b", re.IGNORECASE
)
_REASONING = re.compile(
    r"\b(think|no_think|reason(?:ing)? (?:step|through)|step by step)\b", re.IGNORECASE
)
_SAMPLING = re.compile(
    r"\b(temperature|sampling|temp\s*[=≈:]|deterministic output)\b", re.IGNORECASE
)
_OUTPUT_CONTRACT = re.compile(
    r"\b(output|return|respond with|produce)\b.{0,40}"
    r"\b(only|exactly|format|json|table|file|signature)\b",
    re.IGNORECASE | re.DOTALL,
)

_LONG_PROMPT_CHARS = 800
_A4_MIN_CHARS = 200


def prompt_ref(p: Prompt) -> str:
    return f"{p.source.value}:{p.session_id}:{p.message_ref}"


def score_prompt_deterministic(p: Prompt) -> list[RuleScore]:
    """Structural rules, checkable from text alone."""
    text = p.content
    scores: list[RuleScore] = []

    def add(rule: str, score: float | None, note: str = "") -> None:
        scores.append(
            RuleScore(
                rule=rule,
                score=score,
                origin=p.origin,
                prompt_ref=prompt_ref(p),
                judge="deterministic",
                note=note,
            )
        )

    # A4 structure: only meaningful once a prompt is long enough to need it.
    if len(text) >= _A4_MIN_CHARS:
        structured = bool(_HEADING_OR_LIST.search(text)) or bool(_FENCED_OR_TAGGED.search(text))
        add("A4", 1.0 if structured else 0.0)
    else:
        add("A4", None, "short prompt, structure not required")

    # A5 output contract, A6 examples: task-shaped signals.
    add("A5", 1.0 if (_OUTPUT_CONTRACT.search(text) or has_structured_output(text)) else 0.0)
    add("A6", 1.0 if has_example(text) else 0.0)

    # A11: long prompts should restate the ask near the end, not bury it.
    if len(text) >= _LONG_PROMPT_CHARS:
        tail = text[-max(200, len(text) // 5) :]
        add(
            "A11",
            (
                1.0
                if (_OUTPUT_CONTRACT.search(tail) or _VERIFY.search(tail) or has_constraints(tail))
                else 0.0
            ),
        )
    else:
        add("A11", None, "not a long prompt")

    # Orchestration rules: scored only for machine-authored task specs.
    if p.origin is PromptOrigin.MACHINE:
        add("A7", 1.0 if _REASONING.search(text) else 0.0)
        add("A9", 1.0 if _VERIFY.search(text) else 0.0)
        add("A10", 1.0 if _SELF_CHECK.search(text) else 0.0)
        add("A13", 1.0 if _SAMPLING.search(text) else 0.0)
    else:
        for rule in _ORCHESTRATION_RULES:
            add(rule, None, "orchestration-only rule")

    add("A12", None, "not observable from prompt text")
    return scores


def stratified_sample(prompts: Sequence[Prompt], size: int, seed: int = 1337) -> list[Prompt]:
    """Deterministic sample balanced across (source, origin) strata, recency-first
    within each stratum so coaching reflects how you prompt now."""
    strata: dict[tuple[str, str], list[Prompt]] = {}
    for p in prompts:
        strata.setdefault((p.source.value, p.origin.value), []).append(p)
    for group in strata.values():
        group.sort(key=lambda p: p.timestamp, reverse=True)

    rng = random.Random(seed)  # noqa: S311 - sampling, not cryptography
    total = sum(len(g) for g in strata.values())
    if total <= size:
        return [p for g in strata.values() for p in g]

    picked: list[Prompt] = []
    for key in sorted(strata):
        group = strata[key]
        share = max(1, round(size * len(group) / total))
        # Half most-recent, half random from the rest: recency bias with coverage.
        recent = group[: share // 2 + share % 2]
        rest = group[len(recent) :]
        picked.extend(recent)
        picked.extend(rng.sample(rest, min(share // 2, len(rest))))
    return picked[:size]


def _batch_key(batch: Sequence[Prompt], model: str) -> str:
    h = hashlib.sha256()
    for p in sorted(batch, key=lambda p: p.content_hash):
        h.update(p.content_hash.encode())
    h.update(tpl.RUBRIC_JUDGE_VERSION.encode())
    h.update(model.encode())
    return h.hexdigest()


def score_sample_llm(
    prompts: Sequence[Prompt],
    llm: LocalLLM,
    cache: CacheDB,
    *,
    sample_size: int = 150,
    batch_size: int = 5,
    seed: int = 1337,
) -> list[RuleScore]:
    """LLM-judged scores for A1/A2/A3/A8 over a stratified sample. Raises
    LLMUnavailable on the first hard failure; partial results are lost on
    purpose (a half-judged sample would skew the aggregate silently)."""
    sample = stratified_sample(prompts, sample_size, seed)
    scores: list[RuleScore] = []
    for i in range(0, len(sample), batch_size):
        batch = sample[i : i + batch_size]
        key = _batch_key(batch, llm.model)
        payload = cache.get_llm(key)
        if payload is None:
            block = tpl.format_prompts_block([p.content for p in batch])
            payload = llm.complete_json(
                tpl.SYSTEM_ANALYST, tpl.RUBRIC_JUDGE.format(prompts_block=block)
            )
            cache.put_llm(key, payload, llm.model, tpl.RUBRIC_JUDGE_VERSION)
        for idx, p in enumerate(batch, 1):
            rules = payload.get(str(idx))
            if not isinstance(rules, dict):
                continue
            for rule in LLM_RULES:
                value = rules.get(rule)
                if isinstance(value, int | float):
                    scores.append(
                        RuleScore(
                            rule=rule,
                            score=max(0.0, min(1.0, float(value))),
                            origin=p.origin,
                            prompt_ref=prompt_ref(p),
                            judge="llm",
                        )
                    )
    return scores


def aggregate(scores: Sequence[RuleScore], sampled_llm: int = 0) -> RubricSummary:
    summaries: list[RuleSummary] = []
    for rule in RULE_TITLES:
        rule_scores = [s for s in scores if s.rule == rule and s.score is not None]
        human = [s.score for s in rule_scores if s.origin is PromptOrigin.HUMAN]
        machine = [s.score for s in rule_scores if s.origin is PromptOrigin.MACHINE]
        best = max(rule_scores, key=lambda s: s.score, default=None)
        worst = min(rule_scores, key=lambda s: s.score, default=None)
        summaries.append(
            RuleSummary(
                rule=rule,
                title=RULE_TITLES[rule],
                human_mean=sum(human) / len(human) if human else None,
                machine_mean=sum(machine) / len(machine) if machine else None,
                coverage=len(rule_scores),
                applicable=bool(rule_scores),
                best_ref=best.prompt_ref if best else None,
                worst_ref=worst.prompt_ref if worst else None,
            )
        )
    return RubricSummary(
        rules=tuple(summaries), sampled_llm=sampled_llm, rubric_version=RUBRIC_VERSION
    )


def run_rubric(
    prompts: Sequence[Prompt],
    llm: LocalLLM | None,
    cache: CacheDB,
    *,
    sample_size: int = 150,
    seed: int = 1337,
) -> RubricSummary:
    """Full rubric pass: deterministic corpus-wide, LLM on a sample when possible."""
    scores: list[RuleScore] = []
    for p in prompts:
        scores.extend(score_prompt_deterministic(p))
    sampled = 0
    if llm is not None:
        try:
            llm_scores = score_sample_llm(prompts, llm, cache, sample_size=sample_size, seed=seed)
            scores.extend(llm_scores)
            sampled = len({s.prompt_ref for s in llm_scores})
        except LLMUnavailable:
            pass  # deterministic-only summary; the report banners this
    return aggregate(scores, sampled_llm=sampled)
