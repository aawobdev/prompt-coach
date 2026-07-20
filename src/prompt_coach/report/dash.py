"""Terminal dashboard rendering (rich).

Deterministic-only by design: no LLM calls, and no prompt content ever
reaches the screen (counts, rates, and scores only). LLM-backed insights
belong in `report`. rich degrades cleanly on non-TTY output; `--plain`
forces that path.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from rich.columns import Columns
from rich.console import Group, RenderableType
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from prompt_coach.models import Prompt, RubricSummary, SegmentMetrics, StyleMetrics

_BLOCKS = " ▁▂▃▄▅▆▇█"


def sparkline(values: Sequence[int]) -> str:
    if not values:
        return ""
    peak = max(values)
    if peak == 0:
        return _BLOCKS[1] * len(values)
    return "".join(_BLOCKS[1 + round((v / peak) * 7)] for v in values)


def weekly_volumes(
    prompts: Sequence[Prompt], weeks: int = 12, now: datetime | None = None
) -> dict[str, list[int]]:
    """Per-store prompt counts in `weeks` weekly buckets, oldest first."""
    now = now or datetime.now(tz=UTC)
    start = now - timedelta(weeks=weeks)
    buckets: dict[str, list[int]] = {}
    for p in prompts:
        if p.timestamp < start:
            continue
        idx = min(weeks - 1, int((p.timestamp - start).days // 7))
        buckets.setdefault(p.source.value, [0] * weeks)[idx] += 1
    return dict(sorted(buckets.items()))


def _score_text(value: float | None) -> Text:
    if value is None:
        return Text("n/a", style="dim")
    style = "green" if value >= 0.7 else "yellow" if value >= 0.4 else "red"
    return Text(f"{value:.2f}", style=style)


def _rate(seg: SegmentMetrics | None, attr: str, pct: bool = True) -> str:
    if seg is None:
        return "-"
    value = getattr(seg, attr)
    return f"{value:.0%}" if pct else f"{value:.1f}"


def build_dash(
    *,
    metrics: StyleMetrics,
    rubric: RubricSummary,
    volumes: dict[str, list[int]],
    prompt_count: int,
    session_count: int,
    since_label: str,
) -> RenderableType:
    header = Text.assemble(
        ("prompt-coach", "bold cyan"),
        ("  |  ", "dim"),
        (f"{prompt_count} prompts", "bold"),
        (f" across {session_count} sessions  ", ""),
        (f"({since_label})", "dim"),
    )

    volume = Table(title="Volume (12 weeks)", title_justify="left", show_header=False, box=None)
    volume.add_column("store", style="bold")
    volume.add_column("trend", no_wrap=True)
    volume.add_column("total", justify="right", style="dim")
    for store, counts in volumes.items():
        volume.add_row(store, Text(sparkline(counts), style="cyan"), str(sum(counts)))
    if not volumes:
        volume.add_row("no prompts in range", "", "")

    split = Table(title="Human vs machine", title_justify="left", show_header=True, box=None)
    split.add_column("")
    split.add_column("Human", justify="right")
    split.add_column("Machine", justify="right")
    human, machine = metrics.human, metrics.machine
    split.add_row(
        "Prompts",
        str(human.prompt_count if human else 0),
        str(machine.prompt_count if machine else 0),
    )
    for label, attr, pct in (
        ("Median est. tokens", "median_prompt_tokens", False),
        ("Refinement rate", "refinement_rate", True),
        ("Example rate", "example_rate", True),
        ("Constraint rate", "constraint_rate", True),
        ("Structured output", "structured_output_rate", True),
    ):
        split.add_row(label, _rate(human, attr, pct), _rate(machine, attr, pct))

    scorecard = Table(
        title=f"Rubric scorecard ({rubric.rubric_version}, deterministic rules)",
        title_justify="left",
        box=None,
    )
    scorecard.add_column("Rule", style="bold")
    scorecard.add_column("")
    scorecard.add_column("Human", justify="right")
    scorecard.add_column("Machine", justify="right")
    scorecard.add_column("n", justify="right", style="dim")
    for r in rubric.rules:
        if not r.applicable:
            continue
        scorecard.add_row(
            r.rule, r.title, _score_text(r.human_mean), _score_text(r.machine_mean), str(r.coverage)
        )

    return Group(
        header,
        Text(),
        Columns([Panel(volume), Panel(split)], equal=False),
        Panel(scorecard),
    )
