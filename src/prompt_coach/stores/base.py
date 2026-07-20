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
        HANDOFF\s*:
    )""",
    re.VERBOSE,
)


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
