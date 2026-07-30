"""Model-fit tests: tier classification, demand estimation, mismatch
detection, mode gating, and per-source suggestion scoping."""

from datetime import UTC, datetime

from prompt_coach.analysis.model_fit import (
    available_models,
    classify_model_tier,
    detect_mismatches,
    estimate_demand_tier,
)
from prompt_coach.models import Prompt, PromptOrigin, SourceKind
from prompt_coach.stores.base import content_hash


def make(content: str, model: str | None, source=SourceKind.CLAUDE_CODE, session="s1", ref="0"):
    return Prompt(
        source=source,
        session_id=session,
        message_ref=ref,
        content=content,
        content_hash=content_hash(content),
        timestamp=datetime(2026, 7, 1, 12, 0, 0, tzinfo=UTC),
        origin=PromptOrigin.HUMAN,
        model=model,
    )


# -- classify_model_tier -----------------------------------------------------


def test_claude_family_ladder():
    assert classify_model_tier("claude-haiku-4-5") == "small"
    assert classify_model_tier("claude-sonnet-5") == "medium"
    assert classify_model_tier("claude-opus-4-8") == "large"
    assert classify_model_tier("copilot/claude-haiku-4.5") == "small"


def test_local_model_by_param_count():
    assert classify_model_tier("phi4:14b") == "medium"
    assert classify_model_tier("qwen3-coder-30b:latest") == "medium"
    assert classify_model_tier("gemma4:26b") == "medium"
    assert classify_model_tier("llama3:8b") == "small"
    assert classify_model_tier("llama3:70b") == "large"


def test_unclassifiable_models_return_none():
    assert classify_model_tier(None) is None
    assert classify_model_tier("") is None
    assert classify_model_tier("gpt-5-codex") is None  # no published/inferable size ladder
    assert classify_model_tier("claude-fable-5") is None  # not in the documented ladder
    assert classify_model_tier("copilot/auto") is None  # not attributable to one model


# -- estimate_demand_tier -----------------------------------------------------


def test_demand_tier_buckets():
    assert estimate_demand_tier("short") == "low"
    assert estimate_demand_tier("x" * 500) == "medium"
    assert estimate_demand_tier("x" * 1500) == "high"


# -- available_models ---------------------------------------------------------


def test_available_models_derived_per_source_not_configured():
    prompts = [
        make("a" * 50, "claude-sonnet-5", source=SourceKind.CLAUDE_CODE),
        make("b" * 50, "claude-opus-4-8", source=SourceKind.CLAUDE_CODE),
        make("c" * 50, "gemma4:26b", source=SourceKind.COPILOT, ref="1"),
        make("d" * 50, None, source=SourceKind.COPILOT, ref="2"),  # no model: excluded
    ]
    seen = available_models(prompts)
    assert seen["claude-code"] == ("claude-opus-4-8", "claude-sonnet-5")
    assert seen["copilot"] == ("gemma4:26b",)


# -- detect_mismatches ---------------------------------------------------------


def test_mode_off_returns_nothing():
    prompts = [make("x" * 1500, "llama3:8b")]
    summary = detect_mismatches(prompts, "off")
    assert summary.findings == ()
    assert summary.eligible == 0
    assert summary.coverage == 0


def test_underpowered_flagged():
    prompts = [make("x" * 1500, "llama3:8b")]  # high demand, small model
    summary = detect_mismatches(prompts, "descriptive")
    assert summary.eligible == 1
    assert summary.coverage == 1
    assert len(summary.findings) == 1
    f = summary.findings[0]
    assert f.direction == "underpowered"
    assert f.demand_tier == "high"
    assert f.model_tier == "small"
    assert f.suggestion is None  # descriptive mode never suggests


def test_overpowered_flagged():
    prompts = [make("a" * 50, "claude-opus-4-8")]  # low demand, large model
    summary = detect_mismatches(prompts, "descriptive")
    assert summary.findings[0].direction == "overpowered"


def test_matched_demand_and_tier_not_flagged():
    prompts = [make("x" * 500, "claude-sonnet-5")]  # medium demand, medium tier
    summary = detect_mismatches(prompts, "descriptive")
    assert summary.coverage == 1
    assert summary.findings == ()


def test_unclassifiable_model_excluded_from_coverage_not_flagged_as_a_match():
    prompts = [make("x" * 1500, "gpt-5-codex")]
    summary = detect_mismatches(prompts, "descriptive")
    assert summary.eligible == 1
    assert summary.coverage == 0
    assert summary.findings == ()


def test_micro_replies_and_machine_prompts_excluded():
    prompts = [
        make("y", "llama3:8b"),  # under 40 chars
        Prompt(
            source=SourceKind.CLAUDE_CODE,
            session_id="s1",
            message_ref="m",
            content="TASK: " + "x" * 1500,
            content_hash=content_hash("m"),
            timestamp=datetime(2026, 7, 1, tzinfo=UTC),
            origin=PromptOrigin.MACHINE,
            model="llama3:8b",
        ),
    ]
    summary = detect_mismatches(prompts, "descriptive")
    assert summary.eligible == 0


def test_prescriptive_suggests_same_source_already_used_model():
    prompts = [
        make("x" * 1500, "llama3:8b", ref="0"),  # underpowered: needs a suggestion
        make("y" * 50, "llama3:70b", source=SourceKind.CLAUDE_CODE, ref="1"),  # available peer
    ]
    summary = detect_mismatches(prompts, "prescriptive")
    finding = next(f for f in summary.findings if f.direction == "underpowered")
    assert finding.suggestion == "llama3:70b"  # no medium peer observed, so large is offered


def test_prescriptive_never_suggests_a_model_from_a_different_source():
    prompts = [
        make("x" * 1500, "llama3:8b", source=SourceKind.CLAUDE_CODE, ref="0"),
        make("y" * 50, "claude-opus-4-8", source=SourceKind.COPILOT, ref="1"),
    ]
    summary = detect_mismatches(prompts, "prescriptive")
    finding = summary.findings[0]
    assert finding.suggestion is None  # the only peer observed is on a different store


def test_prescriptive_prefers_medium_over_swinging_to_the_opposite_extreme():
    prompts = [
        make("x" * 1500, "llama3:8b", ref="0"),
        make("y" * 50, "llama3:70b", ref="1"),  # large
        make("z" * 50, "qwen3-coder-30b:latest", ref="2"),  # medium
    ]
    summary = detect_mismatches(prompts, "prescriptive")
    finding = next(f for f in summary.findings if f.direction == "underpowered")
    assert finding.suggestion == "qwen3-coder-30b:latest"
