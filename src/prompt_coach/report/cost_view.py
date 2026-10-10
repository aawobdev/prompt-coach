"""Renderers for `prompt-coach cost`: rich table, plain text and JSON.

No prompt content anywhere: session ids, folders, models, counts and
estimated money only (privacy rule). Every surface carries the disclaimer
and the billed fraction used.
"""

from __future__ import annotations

from collections.abc import Sequence

from prompt_coach.analysis.cost import CostGroup, CostSummary, folder_of
from prompt_coach.models import SessionUsage, score_band
from prompt_coach.pricing import normalise_model

Scores = dict  # (source, session_id) -> {rule: SessionRuleScore}

DISCLAIMER = "Estimates from list prices; not a bill."


def plural(n: int, noun: str) -> str:
    return f"{n:,} {noun}" if n == 1 else f"{n:,} {noun}s"


def money(value: float | None, currency: str) -> str:
    return "n/a" if value is None else f"{currency} {value:,.2f}"


def _ctx(tokens: float) -> str:
    return f"{tokens / 1000:,.0f}k" if tokens >= 1000 else f"{tokens:,.0f}"


def _short_id(session_id: str) -> str:
    return session_id if len(session_id) <= 12 else session_id[:12] + "…"


def _short_model(model: str | None) -> str:
    return normalise_model(model).removeprefix("claude-") if model else "?"


def header_lines(summary: CostSummary, label: str) -> list[str]:
    c = summary.cost
    stores = len({s.source for s in summary.sessions})
    fraction = (
        f"list x {c.billed_fraction:g}"
        + (" (per-store overrides)" if c.billed_fraction_by_store else "")
        + (f" x {c.usd_rate:g}" if c.usd_rate != 1.0 else "")
    )
    lines = [
        f"Cost · {label} · {plural(len(summary.sessions), 'session')}"
        f" · {plural(stores, 'store')}"
        f" · {c.currency} est. ({fraction})",
        f"{DISCLAIMER} Price table: {c.price_table_date}.",
    ]
    if summary.no_usage:
        parts = ", ".join(f"{src} ({plural(n, 'session')})" for src, n in summary.no_usage.items())
        lines.append(f"No usage data: {parts}")
    if summary.unpriced_models:
        parts = ", ".join(
            f"{m} ({plural(n, 'call')})" for m, n in list(summary.unpriced_models.items())[:4]
        )
        more = len(summary.unpriced_models) - 4
        lines.append(f"No price: {parts}" + (f" +{more} more" if more > 0 else ""))
    return lines


def footer_lines(summary: CostSummary) -> list[str]:
    c = summary.cost
    n = len(summary.sessions)
    lines = [
        f"Total: {money(summary.total, c.currency)} est."
        f" · overhead (sub-agents, background tasks) {money(summary.overhead, c.currency)}"
        f" · Opus/Fable {summary.top_tier_share:.0%}"
        f" · sessions over {c.context_warn_tokens // 1000}k context: {summary.large_sessions}"
        f" of {n} = {summary.large_share:.0%} of spend",
        f"Biggest lever: {summary.lever.text}",
    ]
    return lines


def _rule_value(scores: Scores | None, s: SessionUsage, rule: str) -> float | None:
    entry = (scores or {}).get((s.source.value, s.session_id), {}).get(rule)
    return entry.score if entry else None


def _band(scores: Scores | None, s: SessionUsage, rule: str) -> str:
    return score_band(_rule_value(scores, s, rule))[0] if scores else ""


def session_rows(
    sessions: Sequence[SessionUsage], currency: str, scores: Scores | None = None
) -> list[list[str]]:
    rows = []
    for i, s in enumerate(sessions, 1):
        cost = money(s.est_cost, currency).split(" ", 1)[-1]
        if s.unpriced_calls and s.est_cost is not None:
            cost += "+"  # partial: some calls had no price
        rows.append(
            [
                str(i),
                _short_id(s.session_id),
                s.source.value,
                folder_of(s.cwd),
                _short_model(s.model),
                f"{s.api_calls:,}",
                _ctx(s.context_per_call),
                cost,
                _band(scores, s, "A15"),
                _band(scores, s, "A16"),
            ]
        )
    return rows


SESSION_COLUMNS = (
    "#",
    "Session",
    "Store",
    "Folder",
    "Model",
    "Calls",
    "Ctx/call",
    "Est",
    "A15",
    "A16",
)


def group_rows(groups: Sequence[CostGroup], total: float) -> list[list[str]]:
    rows = []
    for g in groups:
        share = "" if g.est_cost is None or not total else f"{g.est_cost / total:.0%}"
        if share == "0%" and g.est_cost:
            share = "<1%"
        rows.append(
            [
                g.key,
                f"{g.sessions:,}",
                f"{g.calls:,}",
                "n/a" if g.est_cost is None else f"{g.est_cost:,.2f}",
                share,
            ]
        )
    return rows


GROUP_COLUMNS = ("{by}", "Sessions", "Calls", "Est", "Share")
_RIGHT = {"#", "Calls", "Ctx/call", "Est", "Sessions", "Share"}


def render_plain(
    summary: CostSummary,
    label: str,
    *,
    by: str,
    top: int,
    groups: Sequence[CostGroup] = (),
    scores: Scores | None = None,
) -> str:
    out = header_lines(summary, label) + [""]
    if by == "session":
        cols = list(SESSION_COLUMNS)
        cols[7] = f"Est {summary.cost.currency}"
        out.append(f"Top {min(top, len(summary.sessions))} sessions by cost")
        rows = session_rows(summary.sessions[:top], summary.cost.currency, scores)
    else:
        cols = [GROUP_COLUMNS[0].format(by=by.capitalize()), *GROUP_COLUMNS[1:]]
        cols[3] = f"Est {summary.cost.currency}"
        out.append(f"Cost by {by}")
        rows = group_rows(groups, summary.total)
    widths = [max(len(str(x)) for x in [c, *(r[i] for r in rows)]) for i, c in enumerate(cols)]

    def line(cells: Sequence[str]) -> str:
        return "  ".join(
            str(cell).rjust(w) if col.split(" ")[0] in _RIGHT else str(cell).ljust(w)
            for cell, w, col in zip(cells, widths, cols, strict=True)
        ).rstrip()

    out += [line(cols), *(line(r) for r in rows), ""]
    out += footer_lines(summary)
    return "\n".join(out)


def render_rich(
    summary: CostSummary,
    label: str,
    *,
    by: str,
    top: int,
    groups: Sequence[CostGroup] = (),
    scores: Scores | None = None,
):
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    head = header_lines(summary, label)
    parts = [Text(head[0], style="bold cyan"), *(Text(h, style="dim") for h in head[1:])]
    if by == "session":
        title = f"Top {min(top, len(summary.sessions))} sessions by cost"
        cols = list(SESSION_COLUMNS)
        cols[7] = f"Est {summary.cost.currency}"
        rows = session_rows(summary.sessions[:top], summary.cost.currency, scores)
    else:
        title = f"Cost by {by}"
        cols = [GROUP_COLUMNS[0].format(by=by.capitalize()), *GROUP_COLUMNS[1:]]
        cols[3] = f"Est {summary.cost.currency}"
        rows = group_rows(groups, summary.total)
    table = Table(box=None, title=title, title_justify="left", title_style="bold")
    for col in cols:
        table.add_column(col, justify="right" if col.split(" ")[0] in _RIGHT else "left")
    colours = {"good": "green", "fair": "yellow", "weak": "red", "n/a": "dim"}
    for r in rows:
        if by == "session":
            r = [*r[:-2], *(Text(v, style=colours.get(v, "")) for v in r[-2:])]
        table.add_row(*r)
    foot = footer_lines(summary)
    parts += [Text(""), table, Text(""), Text(foot[0]), Text(foot[1], style="yellow")]
    return Group(*parts)


def to_json(
    summary: CostSummary,
    *,
    by: str,
    top: int,
    groups: Sequence[CostGroup] = (),
    scores: Scores | None = None,
) -> dict:
    c = summary.cost
    out: dict = {
        "window": {"since": summary.since.date().isoformat() if summary.since else None},
        "params": {
            "currency": c.currency,
            "billed_fraction": c.billed_fraction,
            "billed_fraction_by_store": c.billed_fraction_by_store,
            "usd_rate": c.usd_rate,
            "price_table_date": c.price_table_date,
            "disclaimer": DISCLAIMER,
        },
        "totals": {
            "est_cost": round(summary.total, 4),
            "overhead_cost": round(summary.overhead, 4),
            "sessions": len(summary.sessions),
            "no_usage_sessions": sum(summary.no_usage.values()),
        },
        "no_usage": summary.no_usage,
        "unpriced_models": summary.unpriced_models,
        "lever": {
            "kind": summary.lever.kind,
            "share": round(summary.lever.share, 4),
            "text": summary.lever.text,
        },
    }
    if by == "session":
        out["sessions"] = [
            {
                "source": s.source.value,
                "session_id": s.session_id,
                "folder": folder_of(s.cwd),
                "model": s.model,
                "models": [list(m) for m in s.models],
                "calls": s.api_calls,
                "overhead_calls": s.overhead_calls,
                "context_per_call": round(s.context_per_call),
                "est_cost": None if s.est_cost is None else round(s.est_cost, 4),
                "unpriced_calls": s.unpriced_calls,
                "first": s.first_ts.isoformat(),
                "last": s.last_ts.isoformat(),
                **{rule.lower(): _rule_value(scores, s, rule) for rule in ("A14", "A15", "A16")},
            }
            for s in summary.sessions[:top]
        ]
    else:
        out["groups"] = [
            {
                "key": g.key,
                "sessions": g.sessions,
                "calls": g.calls,
                "est_cost": None if g.est_cost is None else round(g.est_cost, 4),
                "unpriced_calls": g.unpriced_calls,
            }
            for g in groups
        ]
        out["by"] = by
    return out
