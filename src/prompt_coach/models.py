"""Shared types for prompt-coach. Every other module imports its types from here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

LOW_N_THRESHOLD = 3  # coverage below this renders dim + "low n", distinct from n/a


def score_band(value: float | None) -> tuple[str, str]:
    """Shared score-band mapping: (label, color) -- "good"/"fair"/"weak"/"n/a" with a
    rich-compatible color name. One function, used by dash, stats, and report so the
    semantic bands (>=0.7 / >=0.4 / below) never drift between rich and plain-text
    surfaces (D2)."""
    if value is None:
        return "n/a", "dim"
    if value >= 0.7:
        return "good", "green"
    if value >= 0.4:
        return "fair", "yellow"
    return "weak", "red"


def score_label(value: float | None, coverage: int | None = None) -> str:
    """Plain-text score cell: "0.82 good", "n/a", or "0.55 fair (low n)"."""
    label, _ = score_band(value)
    text = f"{value:.2f} {label}" if value is not None else label
    if value is not None and coverage is not None and 0 < coverage < LOW_N_THRESHOLD:
        text += " (low n)"
    return text


class SourceKind(StrEnum):
    HERMES = "hermes"
    CLAUDE_CODE = "claude-code"
    COPILOT = "copilot"
    CODEX = "codex"
    CHATGPT = "chatgpt"
    JSON_IMPORT = "json-import"


class PromptOrigin(StrEnum):
    HUMAN = "human"
    MACHINE = "machine"


@dataclass(frozen=True)
class Prompt:
    """One user prompt, normalised across stores. timestamp is always UTC-aware."""

    source: SourceKind
    session_id: str
    message_ref: str
    content: str
    content_hash: str
    timestamp: datetime
    origin: PromptOrigin
    cwd: str | None = None
    git_repo: str | None = None
    model: str | None = None  # the model that handled this turn, when the store can tell


@dataclass(frozen=True)
class StoreInfo:
    kind: SourceKind
    path: Path
    available: bool
    session_count: int | None = None
    prompt_count: int | None = None
    detail: str = ""


@dataclass
class SyncStats:
    """Per-sync counters. Content never appears here; counts only (privacy rule)."""

    scanned: int = 0
    added: int = 0
    deduped: int = 0
    skipped_malformed: int = 0
    stores_failed: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class SegmentMetrics:
    """Deterministic style metrics for one origin segment."""

    prompt_count: int
    session_count: int
    avg_prompt_tokens: float
    median_prompt_tokens: float
    prompts_per_session: float
    refinement_rate: float
    example_rate: float
    constraint_rate: float
    structured_output_rate: float
    avg_first_message_ratio: float


@dataclass(frozen=True)
class StyleMetrics:
    human: SegmentMetrics | None
    machine: SegmentMetrics | None
    overall: SegmentMetrics | None


@dataclass(frozen=True)
class RuleScore:
    """Score for one prompting-standards rule on one prompt (or N/A)."""

    rule: str  # "A1".."A13"
    score: float | None  # 0.0-1.0, None = not applicable
    origin: PromptOrigin
    prompt_ref: str  # "source:session_id:message_ref"
    judge: str  # "deterministic" | "llm"
    note: str = ""


@dataclass(frozen=True)
class RuleSummary:
    rule: str
    title: str
    human_mean: float | None
    machine_mean: float | None
    coverage: int  # prompts actually scored
    applicable: bool
    best_ref: str | None = None
    worst_ref: str | None = None


@dataclass(frozen=True)
class RubricSummary:
    rules: tuple[RuleSummary, ...]
    sampled_llm: int
    rubric_version: str


@dataclass(frozen=True)
class TopicShare:
    topic: str
    share: float  # 0.0-1.0


@dataclass(frozen=True)
class PatternReport:
    strengths: tuple[str, ...]
    growth_areas: tuple[str, ...]
    notable_patterns: tuple[str, ...]
    topics: tuple[TopicShare, ...]
    sample_size: int


@dataclass(frozen=True)
class DocFinding:
    """Size/structure/staleness read of one project doc (CLAUDE.md/AGENTS.md/README.md)."""

    path: str  # ~-shortened for display; never a prompt, safe to render
    words: int
    headers: int
    list_items: int
    is_redirect: bool  # short stub pointing at another doc in the same dir
    staleness_days: float | None  # None when not in a git repo or file untracked
    flags: tuple[str, ...]  # "sparse" | "unstructured" | "stale"


@dataclass(frozen=True)
class DocQualitySummary:
    findings: tuple[DocFinding, ...]
    dirs_checked: int
    dirs_without_docs: int


@dataclass(frozen=True)
class ModelFitFinding:
    """One prompt where what it demanded and the model that handled it look
    mismatched. No prompt content -- source/model/tiers/direction only, same
    privacy rule as DocFinding."""

    prompt_ref: str  # "source:session_id:message_ref"
    source: SourceKind
    model: str
    demand_tier: str  # "low" | "medium" | "high"
    model_tier: str  # "small" | "medium" | "large"
    direction: str  # "underpowered" | "overpowered"
    suggestion: str | None = None  # only populated in "prescriptive" mode


@dataclass(frozen=True)
class ModelFitSummary:
    mode: str  # "off" | "descriptive" | "prescriptive"
    findings: tuple[ModelFitFinding, ...]
    eligible: int  # human, non-micro-reply prompts considered
    coverage: int  # of eligible, how many had a classifiable model tier
    models_seen: dict[str, tuple[str, ...]]  # source.value -> distinct models observed


@dataclass(frozen=True)
class ReportData:
    generated_at: datetime
    since: datetime | None
    prompt_count: int
    session_count: int
    store_counts: dict[str, int]
    skipped_stores: dict[str, str]
    metrics: StyleMetrics
    rubric: RubricSummary | None  # None when LLM parts unavailable and only
    patterns: PatternReport | None  # deterministic rubric ran (see generator)
    llm_available: bool
    llm_model: str | None
    session_titles: tuple[tuple[str, str], ...] = ()  # (date, title)
    model_fit: ModelFitSummary | None = None  # None when mode is "off"
