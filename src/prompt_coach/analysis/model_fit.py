"""Model fit: does the model that handled a prompt look sized right for what
the prompt demanded? Deterministic only -- no LLM call, the same no-bulk-LLM
discipline rubric.py/patterns.py already follow (see DECISIONS.md
2026-07-29).

"Available models" is never a hardcoded catalog: it is derived empirically,
per store, from models actually observed in the user's own history (see
DECISIONS.md 2026-07-29) -- a prescriptive suggestion never proposes a model
the user hasn't already used with that harness, since you can't act on a
suggestion your tool doesn't offer.

Model tier classification is deliberately conservative: Claude's own public
naming (haiku < sonnet < opus) is a documented ladder, safe to use; local/
open models are sized by the parameter count already in their tag (e.g.
"30b", "24b"); anything else (gpt-5-codex, fable, copilot/auto, or no model
at all) is left unclassified rather than inventing a ranking for models with
no published or inferable capacity signal. Unclassified prompts are excluded
from coverage, not silently scored as a match.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence

from prompt_coach.models import ModelFitFinding, ModelFitSummary, Prompt, PromptOrigin

# Micro-replies ("1", "y") carry no prompting-style signal -- same 40-char
# threshold rubric.py/patterns.py already exclude at (DECISIONS.md 2026-07-20).
_MIN_ELIGIBLE_CHARS = 40

# First-guess thresholds, not calibrated against labelled data -- revisit if
# they prove noisy in practice (same posture as docs.py's _SPARSE_WORDS).
_LOW_DEMAND_CHARS = 200  # matches rubric.py's A4 / nudge.py's own threshold
_HIGH_DEMAND_CHARS = 1200

_SMALL_MAX_B = 9
_MEDIUM_MAX_B = 39

_PARAM_COUNT = re.compile(r"(\d+)b\b", re.IGNORECASE)

# Anthropic's own public naming ladder -- documented, not invented.
_CLAUDE_TIERS = (("haiku", "small"), ("sonnet", "medium"), ("opus", "large"))

# Not a real, single model: Copilot picked one itself, so no tier applies.
_UNATTRIBUTABLE = {"copilot/auto"}


def classify_model_tier(model: str | None) -> str | None:
    """ "small"/"medium"/"large", or None when the model can't be honestly
    classified: unknown family, no size signal in the name, or not
    attributable to a single model at all."""
    if not model or model in _UNATTRIBUTABLE:
        return None
    lowered = model.lower()
    for name, tier in _CLAUDE_TIERS:
        if name in lowered:
            return tier
    m = _PARAM_COUNT.search(lowered)
    if m:
        b = int(m.group(1))
        if b <= _SMALL_MAX_B:
            return "small"
        if b <= _MEDIUM_MAX_B:
            return "medium"
        return "large"
    return None


def estimate_demand_tier(content: str) -> str:
    """ "low"/"medium"/"high" by prompt length -- a coarse complexity proxy,
    not a semantic read of what the prompt actually asks for."""
    n = len(content)
    if n < _LOW_DEMAND_CHARS:
        return "low"
    if n >= _HIGH_DEMAND_CHARS:
        return "high"
    return "medium"


def _eligible(prompts: Sequence[Prompt]) -> list[Prompt]:
    return [
        p
        for p in prompts
        if p.origin is PromptOrigin.HUMAN and len(p.content) >= _MIN_ELIGIBLE_CHARS
    ]


def available_models(prompts: Sequence[Prompt]) -> dict[str, tuple[str, ...]]:
    """Distinct models observed per store -- the only universe a prescriptive
    suggestion may draw from (DECISIONS.md 2026-07-29): never a static list,
    and never a model from a different harness than the mismatch itself."""
    seen: dict[str, set[str]] = defaultdict(set)
    for p in prompts:
        if p.model:
            seen[p.source.value].add(p.model)
    return {source: tuple(sorted(models)) for source, models in seen.items()}


def _suggest(direction: str, candidates: Sequence[tuple[str, str]]) -> str | None:
    """Pick an already-used, same-source model whose tier is a minimal,
    sufficient correction -- prefer landing on "medium" over swinging to the
    opposite extreme. `candidates` is (model, tier) pairs, current model
    already excluded."""
    by_tier = {tier: model for model, tier in candidates}  # any same-tier peer works; last wins
    if direction == "underpowered":
        return by_tier.get("medium") or by_tier.get("large")
    return by_tier.get("medium") or by_tier.get("small")


def detect_mismatches(prompts: Sequence[Prompt], mode: str) -> ModelFitSummary:
    """The one entry point: mirrors run_rubric/detect_patterns's shape.
    mode="off" skips analysis entirely; "descriptive" flags mismatches only;
    "prescriptive" also suggests a same-source, already-used replacement."""
    models_seen = available_models(prompts)
    if mode == "off":
        return ModelFitSummary(
            mode=mode, findings=(), eligible=0, coverage=0, models_seen=models_seen
        )

    eligible = _eligible(prompts)
    findings: list[ModelFitFinding] = []
    coverage = 0
    for p in eligible:
        model_tier = classify_model_tier(p.model)
        if model_tier is None:
            continue
        coverage += 1
        demand_tier = estimate_demand_tier(p.content)
        if demand_tier == "high" and model_tier == "small":
            direction = "underpowered"
        elif demand_tier == "low" and model_tier == "large":
            direction = "overpowered"
        else:
            continue

        suggestion = None
        if mode == "prescriptive":
            candidates = []
            for m in models_seen.get(p.source.value, ()):
                if m == p.model:
                    continue
                t = classify_model_tier(m)
                if t is not None:
                    candidates.append((m, t))
            suggestion = _suggest(direction, candidates)

        findings.append(
            ModelFitFinding(
                prompt_ref=f"{p.source.value}:{p.session_id}:{p.message_ref}",
                source=p.source,
                model=p.model or "",
                demand_tier=demand_tier,
                model_tier=model_tier,
                direction=direction,
                suggestion=suggestion,
            )
        )

    return ModelFitSummary(
        mode=mode,
        findings=tuple(findings),
        eligible=len(eligible),
        coverage=coverage,
        models_seen=models_seen,
    )
