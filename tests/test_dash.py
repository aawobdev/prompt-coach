"""Dash rendering tests: sparkline, weekly buckets, renderable content, CLI."""

from datetime import UTC, datetime, timedelta

from rich.console import Console

from prompt_coach.analysis.metrics import compute_metrics
from prompt_coach.analysis.rubric import aggregate, score_prompt_deterministic
from prompt_coach.models import (
    DocFinding,
    DocQualitySummary,
    Prompt,
    PromptOrigin,
    SourceKind,
    score_band,
    score_label,
)
from prompt_coach.report.dash import build_dash, sparkline, weekly_volumes
from prompt_coach.stores.base import content_hash

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def make(content, source=SourceKind.HERMES, weeks_ago=0, session="s1", ref="0"):
    return Prompt(
        source=source,
        session_id=session,
        message_ref=ref,
        content=content,
        content_hash=content_hash(content),
        timestamp=NOW - timedelta(weeks=weeks_ago),
        origin=PromptOrigin.HUMAN,
    )


class TestSparkline:
    def test_scales_to_peak(self):
        line = sparkline([0, 4, 8])
        assert len(line) == 3
        assert line[2] == "█"
        assert line[0] == "▁"

    def test_all_zero_and_empty(self):
        assert sparkline([0, 0]) == "▁▁"
        assert sparkline([]) == ""


class TestWeeklyVolumes:
    def test_buckets_by_store_oldest_first(self):
        prompts = [
            make("a", weeks_ago=0, ref="1"),
            make("b", weeks_ago=0, ref="2"),
            make("c", weeks_ago=12, ref="3"),
            make("d", source=SourceKind.COPILOT, weeks_ago=1, ref="4"),
            make("too old", weeks_ago=30, ref="5"),
        ]
        vols = weekly_volumes(prompts, weeks=12, now=NOW)
        assert set(vols) == {"hermes", "copilot"}
        assert sum(vols["hermes"]) == 3
        assert vols["hermes"][0] == 1  # the 12-weeks-ago prompt, oldest bucket
        assert vols["hermes"][-1] == 2
        assert sum(vols["copilot"]) == 1


def render(renderable, width=120) -> str:
    console = Console(record=True, width=width, force_terminal=False, no_color=True)
    console.print(renderable)
    return console.export_text()


class TestBuildDash:
    def test_contains_all_panels_and_no_content(self):
        prompts = [
            make(
                "Write a slugify function. Output only the function. Example: e.g. x == y. "
                "Must use stdlib only because deps are frozen.",
                ref="1",
            ),
            make("secret prompt text that must never render", ref="2", weeks_ago=1),
        ]
        scores = [s for p in prompts for s in score_prompt_deterministic(p)]
        text = render(
            build_dash(
                metrics=compute_metrics(prompts),
                rubric=aggregate(scores),
                volumes=weekly_volumes(prompts, now=NOW),
                prompt_count=2,
                session_count=1,
                since_label="since 2026-04-27",
            )
        )
        assert "prompt-coach" in text
        assert "Volume (12 weeks)" in text
        assert "Human vs machine" in text
        assert "Rubric scorecard" in text
        assert "Output contract" in text  # A5 row present
        assert "A12" not in text  # inapplicable rules hidden
        assert "secret prompt text" not in text  # never render content

    def test_empty_volumes_handled(self):
        prompts = [make("hello there friend", ref="1", weeks_ago=30)]
        scores = [s for p in prompts for s in score_prompt_deterministic(p)]
        text = render(
            build_dash(
                metrics=compute_metrics(prompts),
                rubric=aggregate(scores),
                volumes={},
                prompt_count=1,
                session_count=1,
                since_label="all time",
            )
        )
        assert "no prompts in range" in text

    def test_low_coverage_row_flagged_low_n(self):
        prompts = [
            make(
                "Write a slugify function. Output only the function. Example: e.g. x == y. "
                "Must use stdlib only because deps are frozen.",
                ref="1",
            )
        ]
        scores = [s for p in prompts for s in score_prompt_deterministic(p)]
        text = render(
            build_dash(
                metrics=compute_metrics(prompts),
                rubric=aggregate(scores),
                volumes=weekly_volumes(prompts, now=NOW),
                prompt_count=1,
                session_count=1,
                since_label="all time",
            )
        )
        assert "low n" in text  # coverage=1 on every scored rule (D5)

    def test_docs_clean_one_liner_when_no_findings(self):
        prompts = [make("hello there friend", ref="1")]
        docs = DocQualitySummary(
            findings=(
                DocFinding(
                    path="proj/CLAUDE.md",
                    words=200,
                    headers=3,
                    list_items=2,
                    is_redirect=False,
                    staleness_days=5,
                    flags=(),
                ),
            ),
            dirs_checked=1,
            dirs_without_docs=0,
        )
        text = render(
            build_dash(
                metrics=compute_metrics(prompts),
                rubric=aggregate([]),
                volumes=weekly_volumes(prompts, now=NOW),
                prompt_count=1,
                session_count=1,
                since_label="all time",
                docs=docs,
            )
        )
        assert "docs · clean" in text
        assert "docs quality" not in text

    def test_docs_panel_shown_only_when_flagged(self):
        prompts = [make("hello there friend", ref="1")]
        docs = DocQualitySummary(
            findings=(
                DocFinding(
                    path="proj/CLAUDE.md",
                    words=10,
                    headers=0,
                    list_items=0,
                    is_redirect=False,
                    staleness_days=None,
                    flags=("sparse",),
                ),
            ),
            dirs_checked=1,
            dirs_without_docs=0,
        )
        text = render(
            build_dash(
                metrics=compute_metrics(prompts),
                rubric=aggregate([]),
                volumes=weekly_volumes(prompts, now=NOW),
                prompt_count=1,
                session_count=1,
                since_label="all time",
                docs=docs,
            )
        )
        assert "docs quality · 1 finding" in text
        assert "docs · clean" not in text

    def test_claude_code_pinned_first_in_volume_order(self):
        prompts = [
            make("a", source=SourceKind.COPILOT, ref="1"),
            make("b", source=SourceKind.CLAUDE_CODE, ref="2"),
        ]
        vols = weekly_volumes(prompts, now=NOW)
        text = render(
            build_dash(
                metrics=compute_metrics(prompts),
                rubric=aggregate([]),
                volumes=vols,
                prompt_count=2,
                session_count=2,
                since_label="all time",
            )
        )
        assert text.index("claude-code") < text.index("copilot")

    def test_narrow_width_collapses_extra_stores(self):
        prompts = [
            make(f"p{i}", source=s, ref=str(i))
            for i, s in enumerate(
                [SourceKind.HERMES, SourceKind.COPILOT, SourceKind.CODEX, SourceKind.CLAUDE_CODE]
            )
        ]
        vols = weekly_volumes(prompts, now=NOW)
        text = render(
            build_dash(
                metrics=compute_metrics(prompts),
                rubric=aggregate([]),
                volumes=vols,
                prompt_count=4,
                session_count=4,
                since_label="all time",
                width=80,
            ),
            width=80,
        )
        assert "more…" in text

    def test_plain_drops_sparkline_column(self):
        prompts = [make("hello", weeks_ago=w, ref=str(w)) for w in range(5)]
        vols = weekly_volumes(prompts, now=NOW)
        text = render(
            build_dash(
                metrics=compute_metrics(prompts),
                rubric=aggregate([]),
                volumes=vols,
                prompt_count=5,
                session_count=5,
                since_label="all time",
                plain=True,
            )
        )
        assert not any(block in text for block in "▁▂▃▄▅▆▇█")

    def test_store_count_and_stale_suffix_in_header(self):
        prompts = [make("hello", ref="1")]
        text = render(
            build_dash(
                metrics=compute_metrics(prompts),
                rubric=aggregate([]),
                volumes=weekly_volumes(prompts, now=NOW),
                prompt_count=1,
                session_count=1,
                since_label="all time",
                store_count=4,
                stale_count=1,
            )
        )
        assert "4 stores" in text
        assert "1 stale" in text


class TestScoreBand:
    def test_bands_and_labels(self):
        assert score_band(0.82) == ("good", "green")
        assert score_band(0.55) == ("fair", "yellow")
        assert score_band(0.2) == ("weak", "red")
        assert score_band(None) == ("n/a", "dim")

    def test_label_low_n_suffix(self):
        assert score_label(0.9, coverage=2) == "0.90 good (low n)"
        assert score_label(0.9, coverage=10) == "0.90 good"
        assert score_label(None) == "n/a"
