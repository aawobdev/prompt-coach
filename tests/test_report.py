"""Report generator tests: full and degraded renders."""

from datetime import UTC, datetime

from prompt_coach.analysis.metrics import compute_metrics
from prompt_coach.analysis.rubric import aggregate, score_prompt_deterministic
from prompt_coach.models import (
    PatternReport,
    Prompt,
    PromptOrigin,
    ReportData,
    SourceKind,
    TopicShare,
)
from prompt_coach.report.generator import build_report
from prompt_coach.stores.base import content_hash


def make_prompt(content, origin=PromptOrigin.HUMAN, session="s1"):
    return Prompt(
        source=SourceKind.HERMES,
        session_id=session,
        message_ref="1",
        content=content,
        content_hash=content_hash(content),
        timestamp=datetime(2026, 7, 10, tzinfo=UTC),
        origin=origin,
    )


def make_data(**overrides) -> ReportData:
    prompts = [
        make_prompt("Write a function. Output only JSON. Example: e.g. {}"),
        make_prompt("TASK: do a thing\nVerify: it works", PromptOrigin.MACHINE, "s2"),
    ]
    scores = [s for p in prompts for s in score_prompt_deterministic(p)]
    defaults = dict(
        generated_at=datetime(2026, 7, 20, tzinfo=UTC),
        since=datetime(2026, 6, 20, tzinfo=UTC),
        prompt_count=2,
        session_count=2,
        store_counts={"hermes": 1, "claude-code": 1},
        skipped_stores={},
        metrics=compute_metrics(prompts),
        rubric=aggregate(scores, sampled_llm=2),
        patterns=PatternReport(
            strengths=("states output format",),
            growth_areas=("bundles unrelated asks",),
            notable_patterns=("short refinement chains",),
            topics=(TopicShare("coding", 0.7), TopicShare("homelab", 0.3)),
            sample_size=2,
        ),
        llm_available=True,
        llm_model="qwen3-coder:30b",
        session_titles=(("2026-07-10", "First session"),),
    )
    defaults.update(overrides)
    return ReportData(**defaults)


def test_full_report_sections():
    text = build_report(make_data())
    for heading in (
        "# Prompt Coach Report - 2026-07-20",
        "## Summary",
        "## Style Profile",
        "## Rubric Scorecard",
        "## Coaching Insights",
        "### Strengths",
        "### Growth Areas",
        "### Notable Patterns",
        "## Topic Breakdown",
        "## Sessions This Period",
    ):
        assert heading in text
    assert "LLM unavailable" not in text
    assert "qwen3-coder:30b" in text
    assert "states output format" in text
    assert "| coding | 70% |" in text


def test_degraded_report_banner_and_no_llm_sections():
    text = build_report(make_data(rubric=None, patterns=None, llm_available=False, llm_model=None))
    assert "LLM unavailable - deterministic analysis only" in text
    assert "## Style Profile" in text
    assert "## Coaching Insights" not in text
    assert "## Rubric Scorecard" not in text


def test_skipped_stores_listed():
    text = build_report(make_data(skipped_stores={"hermes": "not found"}))
    assert "skipped hermes: not found" in text


def test_rubric_na_visible():
    text = build_report(make_data())
    assert "not scored" in text  # A12 row
