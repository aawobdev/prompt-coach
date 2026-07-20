"""Dash rendering tests: sparkline, weekly buckets, renderable content, CLI."""

from datetime import UTC, datetime, timedelta

from rich.console import Console

from prompt_coach.analysis.metrics import compute_metrics
from prompt_coach.analysis.rubric import aggregate, score_prompt_deterministic
from prompt_coach.models import Prompt, PromptOrigin, SourceKind
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
