"""Shared types for prompt-coach. Every other module imports its types from here."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path


class SourceKind(StrEnum):
    HERMES = "hermes"
    CLAUDE_CODE = "claude-code"
    COPILOT = "copilot"
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
