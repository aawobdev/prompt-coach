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

from prompt_coach.models import (
    LOW_N_THRESHOLD,
    DocQualitySummary,
    Prompt,
    RubricSummary,
    SegmentMetrics,
    StyleMetrics,
    score_band,
)

_BLOCKS = " ▁▂▃▄▅▆▇█"
_NARROW_WIDTH = 100  # below this, dash stacks panels full-width instead of side-by-side (D3)
_MAX_VOLUME_ROWS = 3  # below _NARROW_WIDTH, collapse the rest into "+ N more…" (1a)


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
    return buckets


def _ordered_stores(volumes: dict[str, list[int]]) -> list[str]:
    """claude-code pinned first (primary usage per project scope), remainder by
    volume descending (D4) -- replaces the previous alphabetical order."""
    return sorted(volumes, key=lambda name: (name != "claude-code", -sum(volumes[name])))


def _score_text(value: float | None, coverage: int | None = None) -> Text:
    label, color = score_band(value)
    text = f"{value:.2f} {label}" if value is not None else label
    low_n = value is not None and coverage is not None and 0 < coverage < LOW_N_THRESHOLD
    if low_n:
        return Text(f"{text} ·low n", style="dim")
    return Text(text, style=color)


def _rate(seg: SegmentMetrics | None, attr: str, pct: bool = True) -> str:
    if seg is None:
        return "-"
    value = getattr(seg, attr)
    return f"{value:.0%}" if pct else f"{value:.1f}"


def _docs_section(docs: DocQualitySummary) -> RenderableType:
    """The clean one-liner replaces silent omission; the findings panel appears
    only when findings exist (D6) -- its presence is itself the alert, so rows stay in
    normal score-band colors and only the title carries the flagged count in yellow."""
    flagged = [f for f in docs.findings if f.flags]
    if not flagged:
        return Text.assemble(
            ("docs · clean", "green"),
            (f" -- 0 flagged findings in window ({len(docs.findings)} checked)", "dim"),
        )
    table = Table(
        title=Text.assemble(
            (f"docs quality · {len(flagged)} finding{'s' if len(flagged) != 1 else ''}", "yellow")
        ),
        title_justify="left",
        box=None,
    )
    table.add_column("Path", style="bold")
    table.add_column("Words", justify="right")
    table.add_column("Flags")
    for f in flagged:
        table.add_row(f.path, str(f.words), ", ".join(f.flags))
    return Panel(table)


def build_dash(
    *,
    metrics: StyleMetrics,
    rubric: RubricSummary,
    volumes: dict[str, list[int]],
    prompt_count: int,
    session_count: int,
    since_label: str,
    store_count: int | None = None,
    stale_count: int = 0,
    docs: DocQualitySummary | None = None,
    width: int = 120,
    plain: bool = False,
) -> RenderableType:
    header_parts: list[tuple[str, str]] = [
        ("prompt-coach", "bold cyan"),
        (" · ", "dim"),
        (f"{prompt_count} prompts", "bold"),
        (f" · {session_count} sessions", ""),
    ]
    if store_count is not None:
        header_parts.append((f" · {store_count} store{'s' if store_count != 1 else ''}", "dim"))
        if stale_count:
            header_parts.append((f" ({stale_count} stale)", "yellow"))
    header_parts.append((f" · {since_label}", "dim"))
    header = Text.assemble(*header_parts)

    ordered_stores = _ordered_stores(volumes)
    narrow = width < _NARROW_WIDTH
    shown, rest = (
        (ordered_stores[:_MAX_VOLUME_ROWS], ordered_stores[_MAX_VOLUME_ROWS:])
        if narrow
        else (ordered_stores, [])
    )
    # Trend column stays present (not removed) even in plain mode, so the table's
    # measured width doesn't shrink enough to word-wrap the panel title (D9 aside:
    # plain still drops the sparkline chars themselves -- they carry no meaning
    # without color -- just not the column that holds them).
    volume = Table(title="Volume (12 weeks)", title_justify="left", show_header=False, box=None)
    volume.add_column("store", style="bold")
    volume.add_column("trend", no_wrap=True)
    volume.add_column("total", justify="right", style="dim")
    for store in shown:
        counts = volumes[store]
        trend = "" if plain else Text(sparkline(counts), style="cyan")
        volume.add_row(store, trend, str(sum(counts)))
    if rest:
        rest_total = sum(sum(volumes[s]) for s in rest)
        volume.add_row(f"+ {len(rest)} more…", "", str(rest_total), style="dim")
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
        low_n = 0 < r.coverage < LOW_N_THRESHOLD
        scorecard.add_row(
            r.rule,
            r.title,
            _score_text(r.human_mean, r.coverage),
            _score_text(r.machine_mean, r.coverage),
            str(r.coverage),
            style="dim" if low_n else None,
        )

    panels = [Panel(volume), Panel(split)]
    top: RenderableType = Group(*panels) if narrow else Columns(panels, equal=False)
    parts: list[RenderableType] = [header, Text(), top, Panel(scorecard)]
    if docs is not None:
        parts.append(_docs_section(docs))
    return Group(*parts)
