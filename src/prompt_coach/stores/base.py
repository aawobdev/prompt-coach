"""SessionStore protocol and helpers shared by all store readers."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from prompt_coach.models import Prompt, PromptOrigin, SourceKind, StoreInfo

# Machine-generated task specs from the orchestration pipeline (hermes -z /
# claude -p one-shots logged as "user" messages). Conservative on purpose:
# a false HUMAN keeps a prompt in the primary segment, a false MACHINE
# silently drops coaching signal.
_MACHINE_PATTERNS = re.compile(
    r"""^\s*(
        TASK\s*[:\d] |
        Task\ spec\s*: |
        One-shot\ task\s*: |
        HANDOFF\s*: |
        CARD\s+\d+\s+of\s+\d+\b
    )""",
    re.VERBOSE,
)


# Harness-injected payloads look exactly like typed prompts, but nobody
# typed them. Single source of truth so nudge.py's live-hook check and
# every store's corpus-read check reject the same wrapper shapes --
# previously nudge.py had its own private copy of this pattern and no
# store checked it at all. bash-input/bash-stdout/bash-stderr and
# artifact-view-context confirmed live 2026-09-24 (code review of the
# promptSource=sdk change below): both slip past claude_code.py's
# system-reminder stripping and land in the human corpus with
# origin.kind=human. task-notification seen live 2026-07-31 in nudge.py
# (a background-task completion event got offered a "tighten this up"
# rewrite). classify_origin (above) catches the orchestration task specs
# (TASK:/HANDOFF: one-shots) on top of this.
# The desktop app's artifact-comment relay opens with a fixed bracketed
# header (then Artifact:/Comment thread:/<comment-anchor-context>...): an
# app-generated payload carrying a comment, not a typed prompt. Seen live
# 2026-09-24, where its metadata labels earned the sections bonus.
HARNESS_WRAPPED = re.compile(
    r"^\s*(?:<(task-notification|command-message|command-name|system-reminder|"
    r"local-command-stdout|artifact-view-context|bash-(input|stdout|stderr))\b"
    r"|\[Artifact comment sent to Claude\])"
)


def is_harness_wrapped(content: str) -> bool:
    return bool(HARNESS_WRAPPED.match(content))


def classify_origin(content: str) -> PromptOrigin:
    if _MACHINE_PATTERNS.match(content):
        return PromptOrigin.MACHINE
    return PromptOrigin.HUMAN


def content_hash(content: str) -> str:
    """Hash of whitespace-normalised content; the fork-dedupe key component."""
    normalised = " ".join(content.split())
    return hashlib.sha256(normalised.encode()).hexdigest()


@runtime_checkable
class SessionStore(Protocol):
    kind: SourceKind

    def discover(self) -> StoreInfo: ...

    def iter_prompts(self, since: datetime | None = None) -> Iterator[Prompt]: ...
