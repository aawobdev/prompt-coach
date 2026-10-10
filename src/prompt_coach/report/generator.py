"""Markdown report rendering. The degraded (no-LLM) path is first-class:
the desktop GPU is frequently off, and the report must stay useful then."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from prompt_coach.analysis.cost import folder_of
from prompt_coach.models import ReportData, score_label
from prompt_coach.report.cost_view import DISCLAIMER, money

_TEMPLATES = Path(__file__).parent / "templates"


def build_report(data: ReportData) -> str:
    env = Environment(  # noqa: S701 - markdown output, autoescape not wanted
        loader=FileSystemLoader(_TEMPLATES),
        undefined=StrictUndefined,
        trim_blocks=False,
        lstrip_blocks=False,
    )
    env.globals["score_label"] = score_label  # D2: shared bands, bold text label in markdown
    env.globals["money"] = money
    env.globals["folder_of"] = folder_of
    env.globals["cost_disclaimer"] = DISCLAIMER
    return env.get_template("report.md.j2").render(data=data)
