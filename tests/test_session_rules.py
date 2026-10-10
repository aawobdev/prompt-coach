"""Session cost rules A14-A16 and the report/dash cost blocks (TEST-PLAN R1-R5, C7, C8)."""

import io
from datetime import UTC, datetime, timedelta

import pytest
from rich.console import Console

from prompt_coach.analysis.cost import aggregate_sessions
from prompt_coach.analysis.cost_section import build_cost_section, weekly_spend
from prompt_coach.analysis.session_rules import (
    a14_right_sized,
    a15_session_size,
    a16_cold_resume,
    score_sessions,
    summarise_rules,
)
from prompt_coach.config import CostConfig
from prompt_coach.models import Prompt, PromptOrigin, SourceKind
from prompt_coach.stores.base import content_hash
from tests.test_cost import T0, rec


def session(calls, ctx, model="claude-sonnet-5"):
    """One aggregated session with `calls` per-call records of `ctx` context."""
    records = [rec(key=str(i), read=ctx, model=model) for i in range(calls)]
    (s,) = aggregate_sessions(records, CostConfig())
    return s


# -- A15 (R1, R2) -----------------------------------------------------------------


def test_a15_context_threshold():
    assert a15_session_size(session(10, 99_000), CostConfig()).score == 1.0
    assert a15_session_size(session(10, 101_000), CostConfig()).score == 0.5


def test_a15_both_exceeded_is_weak():
    assert a15_session_size(session(101, 120_000), CostConfig()).score == 0.0


def test_a15_thresholds_configurable():
    cost = CostConfig(context_warn_tokens=50_000, calls_warn=5)
    assert a15_session_size(session(10, 60_000), cost).score == 0.0


# -- A16 (R3, R4) -----------------------------------------------------------------


def test_a16_three_of_thirty_cold_resumes():
    times, t = [], T0
    for i in range(30):
        t += timedelta(minutes=6 if i in (10, 20, 25) else 1)
        times.append(t)
    records = [rec(key=str(i), read=150_000, ts=ts) for i, ts in enumerate(times)]
    assert a16_cold_resume(records, CostConfig()).score == pytest.approx(0.9)


def test_a16_small_context_gap_is_not_a_cold_resume():
    records = [rec(key=str(i), read=20_000, ts=T0 + timedelta(minutes=10 * i)) for i in range(5)]
    assert a16_cold_resume(records, CostConfig()).score == 1.0


def test_a16_without_gap_data_is_na_not_zero():
    totals = [rec(calls=200, read=150_000, source=SourceKind.HERMES)]
    score = a16_cold_resume(totals, CostConfig())
    assert score.score is None
    assert a16_cold_resume([], CostConfig()).score is None


# -- A14 (R5) -----------------------------------------------------------------------


def _prompt(text, model, i=0, session_id="s1"):
    return Prompt(
        source=SourceKind.CLAUDE_CODE,
        session_id=session_id,
        message_ref=f"m{i}",
        content=text,
        content_hash=content_hash(text),
        timestamp=T0 + timedelta(minutes=i),
        origin=PromptOrigin.HUMAN,
        model=model,
    )


# Low-demand prompts: past model fit's 40-char micro-reply cut, under 200 chars.
SHORT = [
    "rename the variable foo to bar in utils.py please",
    "what does the parse_line function return when the line is empty?",
    "add a short docstring to parse_line explaining its arguments",
    "fix the typo in the readme heading where it says 'instal'",
]


def test_a14_opus_on_short_prompts_is_weak_and_names_the_model():
    prompts = [_prompt(t, "claude-opus-5-5", i) for i, t in enumerate(SHORT)]
    score = a14_right_sized(prompts)
    assert score.score == 0.0
    assert "claude-opus-5-5" in score.note


def test_a14_no_classifiable_model_is_na():
    prompts = [_prompt(t, None, i) for i, t in enumerate(SHORT)]
    assert a14_right_sized(prompts).score is None


def test_a14_fills_missing_models_from_usage_records():
    prompts = [_prompt(t, None, i) for i, t in enumerate(SHORT)]
    records = [
        rec(key=str(i), model="claude-opus-5-5", ts=T0 + timedelta(minutes=i, seconds=5))
        for i in range(4)
    ]
    (s,) = aggregate_sessions(records, CostConfig())
    scores = score_sessions([s], records, prompts, CostConfig())
    assert scores[("claude-code", "s1")]["A14"].score == 0.0


# -- summaries ------------------------------------------------------------------------


def test_rule_means_are_weighted_by_spend():
    big = session(150, 200_000)  # weak on A15, expensive
    small = [rec(session=f"t{i}", key="k", read=1_000) for i in range(9)]
    sessions = [big, *aggregate_sessions(small, CostConfig())]
    scores = {
        (s.source.value, s.session_id): {
            "A14": a14_right_sized([]),
            "A15": a15_session_size(s, CostConfig()),
            "A16": a16_cold_resume([], CostConfig()),
        }
        for s in sessions
    }
    weights = {(s.source.value, s.session_id): s.est_cost for s in sessions}
    a15 = next(r for r in summarise_rules(scores, weights) if r.rule == "A15")
    assert a15.coverage == 10
    assert a15.mean < 0.01  # unweighted it would read 0.9 "good"
    unweighted = next(r for r in summarise_rules(scores) if r.rule == "A15")
    assert unweighted.mean == pytest.approx(0.9)


def test_weekly_spend_buckets():
    now = datetime.now(tz=UTC)
    records = [rec(key="a", inp=1_000_000, ts=now - timedelta(days=1))]
    weekly = weekly_spend(records, CostConfig(), weeks=4, now=now)
    assert weekly == pytest.approx((0, 0, 0, 2.0))


# -- report (C7) and dash (C8) ----------------------------------------------------------


def _report_data(cost_section):
    from prompt_coach.analysis.metrics import compute_metrics
    from prompt_coach.models import ModelFitSummary, ReportData

    prompts = [_prompt(t, "claude-sonnet-5", i) for i, t in enumerate(SHORT)]
    return ReportData(
        generated_at=T0,
        since=None,
        prompt_count=len(prompts),
        session_count=1,
        store_counts={"claude-code": 4},
        skipped_stores={},
        metrics=compute_metrics(prompts),
        rubric=None,
        patterns=None,
        llm_available=False,
        llm_model=None,
        model_fit=ModelFitSummary(
            mode="descriptive", findings=(), eligible=4, coverage=4, models_seen={}
        ),
        cost=cost_section,
    )


def test_report_cost_section_after_model_fit():
    from prompt_coach.report.generator import build_report

    records = [rec(key=str(i), read=150_000) for i in range(3)]
    text = build_report(_report_data(build_cost_section(records, [], CostConfig())))
    assert text.index("## Model Fit") < text.index("## Cost (est.)")
    assert "Biggest lever:" in text
    assert "not a bill" in text
    assert "A15 Session size" in text


def test_report_omits_cost_when_disabled():
    from prompt_coach.report.generator import build_report

    assert "## Cost" not in build_report(_report_data(None))


def test_report_cost_section_without_usage():
    from prompt_coach.report.generator import build_report

    text = build_report(_report_data(build_cost_section([], [], CostConfig())))
    assert "No token usage recorded" in text


def _dash(cost_section):
    from prompt_coach.analysis.metrics import compute_metrics
    from prompt_coach.models import RubricSummary
    from prompt_coach.report.dash import build_dash

    return build_dash(
        metrics=compute_metrics([]),
        rubric=RubricSummary(rules=(), sampled_llm=0, rubric_version="t"),
        volumes={},
        prompt_count=0,
        session_count=0,
        since_label="since 2026-09-01",
        cost=cost_section,
    )


def _render(renderable, html=False):
    console = Console(record=True, width=120, force_terminal=True, file=io.StringIO())
    console.print(renderable)
    return console.export_html() if html else console.export_text()


def test_dash_cost_panel_terminal_and_html():
    records = [rec(key=str(i), read=150_000) for i in range(3)]
    section = build_cost_section(records, [], CostConfig())
    text = _render(_dash(section))
    assert "Cost (since 2026-09-01)" in text
    assert "A15" in text and "Lever:" in text
    assert "not a bill" in _render(_dash(section), html=True)


def test_dash_renders_with_no_usage():
    text = _render(_dash(build_cost_section([], [], CostConfig())))
    assert "cost · no usage data in window" in text
