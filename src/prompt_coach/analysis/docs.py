"""Project documentation quality: size, structure, and staleness of the
CLAUDE.md/AGENTS.md/README.md files that sit alongside your prompt history.

Presence alone has no signal in this corpus (a prior spike found ~97% of
project dirs already have at least one doc file) so this scores the docs
that exist instead. Redirect stubs ("See AGENTS.md.") are real and by
design, not a quality gap -- they are detected and excluded from the
sparse/unstructured flags.
"""

from __future__ import annotations

import re
import subprocess
import time
from collections.abc import Sequence
from pathlib import Path

from prompt_coach.models import DocFinding, DocQualitySummary, Prompt

DOC_NAMES = ("CLAUDE.md", "AGENTS.md", "README.md")
_MAX_WALK_UP = 6
_REDIRECT_MAX_WORDS = 30
_SPARSE_WORDS = 150
_STALE_DAYS = 90.0

_HEADER = re.compile(r"^#{1,6}\s", re.MULTILINE)
_LIST_ITEM = re.compile(r"^\s*([-*]|\d+[.)])\s", re.MULTILINE)


def find_project_docs(cwd: str, home: Path | None = None) -> list[Path]:
    """Doc files at the nearest ancestor of `cwd` that has any, walking up
    to `home` (or _MAX_WALK_UP levels, whichever comes first)."""
    home = home or Path.home()
    current = Path(cwd)
    if not current.exists():
        return []
    for _ in range(_MAX_WALK_UP):
        hits = [current / name for name in DOC_NAMES if (current / name).is_file()]
        if hits:
            return hits
        if current == home or current.parent == current:
            break
        current = current.parent
    return []


def is_redirect_stub(text: str) -> bool:
    """Short doc whose only job is pointing at another doc in the same dir."""
    if len(text.split()) > _REDIRECT_MAX_WORDS:
        return False
    return any(name in text for name in DOC_NAMES)


def git_staleness_days(path: Path) -> float | None:
    """Days since the last commit touching `path`, or None if not tracked
    (no repo, untracked file, or git unavailable)."""
    try:
        args = ["git", "-C", str(path.parent), "log", "-1", "--format=%ct", "--", path.name]
        out = subprocess.run(args, capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    commit_ts = int(out.stdout.strip())
    return max(0.0, (time.time() - commit_ts) / 86400.0)


def score_doc(path: Path, home: Path | None = None) -> DocFinding:
    home = home or Path.home()
    text = path.read_text(errors="ignore")
    words = len(text.split())
    headers = len(_HEADER.findall(text))
    list_items = len(_LIST_ITEM.findall(text))
    redirect = is_redirect_stub(text)
    staleness = git_staleness_days(path)

    flags: list[str] = []
    if not redirect:
        if words < _SPARSE_WORDS:
            flags.append("sparse")
        elif headers == 0 and list_items == 0:
            flags.append("unstructured")
        if staleness is not None and staleness > _STALE_DAYS:
            flags.append("stale")

    display_path = str(path)
    try:
        display_path = f"~/{path.relative_to(home)}"
    except ValueError:
        pass

    return DocFinding(
        path=display_path,
        words=words,
        headers=headers,
        list_items=list_items,
        is_redirect=redirect,
        staleness_days=staleness,
        flags=tuple(flags),
    )


def analyse_docs(prompts: Sequence[Prompt], home: Path | None = None) -> DocQualitySummary:
    home = home or Path.home()
    cwds = {p.cwd for p in prompts if p.cwd}

    doc_paths: set[Path] = set()
    dirs_without_docs = 0
    for cwd in cwds:
        found = find_project_docs(cwd, home)
        if found:
            doc_paths.update(found)
        else:
            dirs_without_docs += 1

    findings = tuple(sorted((score_doc(path, home) for path in doc_paths), key=lambda f: f.path))
    return DocQualitySummary(
        findings=findings,
        dirs_checked=len(cwds),
        dirs_without_docs=dirs_without_docs,
    )
