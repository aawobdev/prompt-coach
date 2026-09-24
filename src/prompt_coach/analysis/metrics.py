"""Deterministic style metrics. Pure functions, no I/O, no LLM.

These run over the FULL corpus (cheap) and are exact and repeatable: they are
the stable baseline that phase-2 trend analysis will diff against, so keep
every heuristic here deterministic and versionable.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from functools import lru_cache

from prompt_coach.models import Prompt, PromptOrigin, SegmentMetrics, StyleMetrics

# Signals a prompt includes a worked example. "e.g." and "example:" sit
# outside the \b-wrapped group deliberately (code review 2026-09-24): a
# trailing \b right after either can never fire, since both end in a
# non-word character (".", ":") and whatever follows (a space, a closing
# paren, end of string) is non-word too, so no word/non-word transition
# exists there -- confirmed both were dead for realistic prose ("e.g. a
# short json blob", "example: baz") on `main`, before this diff touched
# either, with zero test coverage catching it either way. A prior fix
# tried `e\.g\.(?=\s)` inside the \b-wrapped group and stayed just as
# dead. `(?!\w)` fixes both: matches "e.g. ", "(e.g.)", "example: X" and
# end-of-string, and only excludes the case that's arguably not real
# usage anyway (e.g.foo, example:foo glued with no space).
# A code fence is NOT an example cue on its own any more (2026-09-24): a
# fence is where pasted material lives, so a bare ```paste``` used to
# count as "has a worked example" and earn the bonus. Fences now count
# only when the authored text just before them cues an example -- see
# has_example below.
_EXAMPLE = re.compile(
    r"\b(for example|for instance|like this|such as)\b|\be\.g\.(?!\w)|\bexample:(?!\w)|==",
    re.IGNORECASE,
)
# Authored text right before a fence that makes the fenced block a worked
# example rather than pasted input ("expected output like this:",
# "in this format:"). "output" alone is deliberately absent: "here's the
# output:" introduces a paste, not an example.
_EXAMPLE_CUE = re.compile(
    r"\b(?:like this|e\.g\.|for example|for instance|examples?|sample|expected"
    r"|should look like|in this format|format like|template|such as)",
    re.IGNORECASE,
)
_CUE_WINDOW = 120  # chars of authored text before a fence searched for a cue
# A fenced block, closed or running to the end of the message.
_FENCE = re.compile(r"```.*?(?:```|\Z)", re.DOTALL)
# A line that is recognisably machine output rather than something typed:
# log levels, timestamps, tracebacks and stack frames, shell prompts and
# shell-tool errors, padded/dotted key-value columns (PowerShell,
# ipconfig), a space before the colon (`Name : value` -- typed prose
# almost never does that), columnar lines with a 3+ space gap (motd,
# tables), pip's fixed progress lines, JSON, and box-drawing/status
# glyphs (systemd). Calibrated
# against the real Claude Code corpus (2026-09-24): these are the shapes
# that made pasted terminal output look like authored "Label:" sections.
_OUTPUT_LINE = re.compile(
    r"""(?:
        \s*\[?(?:ERROR|ERR|WARN|WARNING|INFO|DEBUG|TRACE|FATAL|CRITICAL|NOTICE)\]?[\s:\]-]
      | \s*\[?\d{4}-\d{2}-\d{2}[\ T]\d{1,2}:\d{2}
      | \s*\[\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AP]M)?\]
      | Traceback\ \(most\ recent\ call\ last\)
      | \s+File\ ".*",\ line\ \d+
      | \s+at\ [\w$.<>]+\(.*\)\s*$
      | [\w.]*(?:Error|Exception)(?::\s.*)?$
      | \s*(?:\$|>>>|PS\ [A-Za-z]:\\[^>]*>)\s+\S
      | [\w.-]+@[\w.-]+:[^\n]*[$\#]\s
      | (?:sudo|bash|-bash|zsh|sh|fish|git|npm|pip|python3?|node|docker|systemctl|ssh|curl):\s
      | \s*[\w .()/-]*?\S\s{2,}:(?:\s|$)
      | \s*[\w.-]+\ +:(?:\s|$)
      | \s*\S.*?\S\s{3,}\S
      | (?:Collecting|Downloading|Requirement\ already\ satisfied
        |Installing\ collected|Successfully\ installed|Using\ cached|Building\ wheel)\b
      | .*(?:\ \.){3,}
      | .*\S\s+\.\s*:(?:\s|$)
      | \s*[{}\[\]]
      | \s*"[^"\n]+"\s*:
      | \s*[\u25cf\u2502\u251c\u2514\u250c\u2510\u2518\u2524\u252c\u2534\u253c\u2500]
    )""",
    re.VERBOSE,
)
_TB_FRAME = re.compile(r'\s+File ".*", line \d+')
# Markers of a pasted terminal session or chat log: a shell prompt line
# (with or without a command after it), a chat header ("Hermes  [7:30 PM]";
# the speaker part has no colon, so a typed "Can you help me fix: Hermes
# [9:00 PM]" line stays authored),
# a bare "[7:30 PM]", WhatsApp's "[15:19, 10/09/2026] Name:"), or Slack's
# "(edited)". Between the first and last marker everything is session or
# chat content, whatever its shape -- line-by-line rules can't tell a bot's
# prose reply or a tool's help text from typed sections (both were still
# earning the sections bonus on the real corpus, 2026-09-24).
_SESSION_MARKER = re.compile(
    r"""\s*(?:
        PS\ [A-Za-z]:\\[^>\n]*>(?:\s|$)
      | [\w.-]+@[\w.-]+:[^\n]*[$\#](?:\s|$)
      | \$(?:\s|$)
      | [^\s\[\]:][^\[\]:\n]{0,30}?\s+\[\d{1,2}:\d{2}(?::\d{2})?\s?(?:[AP]M)?\]\s*$
      | \[\d{1,2}:\d{2}(?::\d{2})?\s?(?:[AP]M)?\]\s*$
      | \[\d{1,2}:\d{2},\ \d{1,2}/\d{1,2}/\d{2,4}\]
      | \(edited\)"?\s*$
    )""",
    re.VERBOSE,
)
# "Label: value" -- 5+ of these in a row is a key-value dump (systemd
# status, motd, form fields pasted from a document), not authored
# sections. 5, not fewer, so a compact typed spec ("Goal: / Context: /
# Steps: / Output:") stays authored.
_KV_LINE = re.compile(r"\s*[A-Za-z][\w ./()-]{0,40}:\s+\S")
_KV_RUN = 5
# Bare paste: at least this much non-space content, of which at most
# _PASTE_AUTHORED_SHARE is authored, and fewer than _PASTE_MAX_WORDS
# authored words -- "mostly pasted, little or no ask".
_MIN_PASTE_CHARS = 60
_PASTE_AUTHORED_SHARE = 0.2
_PASTE_MAX_WORDS = 12
# Signals explicit constraints/limits.
_CONSTRAINT = re.compile(
    r"\b(must|only|never|always|constraint|require[sd]?|max|min|limit|exactly"
    r"|no more than|at least|stdlib only|do not|don't)\b",
    re.IGNORECASE,
)
# A bullet or numbered list item, marker only -- shared with has_sections
# below and with rubric.py's A4 structure check (code review 2026-09-24:
# this exact "is it a list item" pattern existed independently in three
# places -- here, rubric.py, and docs.py -- with different marker sets;
# this is now the one metrics.py/rubric.py both build on).
LIST_ITEM = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s+", re.MULTILINE)
# Implicit constraints from bullet lists and numbered criteria -- but only
# when the item itself states a bound (a number with a unit or a
# comparison, or an explicit limit word), not just "is formatted as a
# list". A prior version matched ANY bullet/numbered line regardless of
# content (code review 2026-09-24: "- resized down\n- stopped\n- migrated"
# -- a plain list of options, no limits at all -- registered as "has
# constraints", which defeated score.py's vague_scope penalty for
# genuinely unconstrained "revamp everything" + bullet-list prompts).
# Real constraint-bearing bullets from this repo's own test fixtures still
# match: "CPU/RAM usage < 30% for 7 days", "idle for > 30 days".
# A percentage counts only next to a bound ("below 50%", "< 30%"): a bare
# figure is a reading, not a limit -- "- Related context: 72%" and
# "3. So I'm not 100%" were counted as constraints on the real corpus
# (2026-09-24).
_IMPLICIT_CONSTRAINT = re.compile(
    LIST_ITEM.pattern
    + r".*(?:\b(?:under|below|over|above|up\s+to|at\s+most|less\s+than|more\s+than"
    r"|max(?:imum)?|min(?:imum)?)\s+\d+(?:\.\d+)?\s*%"
    r"|\d+\s*(?:days?|hours?|minutes?|weeks?|months?|years?)\b"
    r"|[<>]=?\s*\d|\d\s*[<>]=?|\bwithin\b|\bat least\b|\bno more than\b)",
    re.IGNORECASE | re.MULTILINE,
)
# A markdown heading or a short "Label:" line -- general structure signal,
# shared with rubric.py's A4 check the same way LIST_ITEM is (code review
# 2026-09-24: has_sections below used to hardcode a closed 10-word English
# list -- Problem/Expected/Steps/Goal/... -- that couldn't recognize
# "Root Cause:"/"Fix:" or any team's own vocabulary; this generic pattern,
# already proven in rubric.py's A4 scoring, has no such ceiling).
SECTION_LABEL = re.compile(r"^\s*(?:#{1,4}\s|\w[\w /]{0,30}:\s)", re.MULTILINE)
# A section label for has_sections: a markdown heading, or a "Label:" that
# starts with a capital letter, indented at most 2 spaces. Lowercase
# labels are config and CLI output far more often than typed sections
# (YAML keys, "sudo:", "bash:"), and so are deeply indented ones
# (`     Loaded:` in systemctl status) -- both seen in the real corpus
# 2026-09-24.
_SECTION_MIN_VALUE_WORDS = 4
_SECTION_MIN_DENSITY = 0.5  # counted labels per 1,000 chars of the whole prompt
_SECTION_NAME = re.compile(r"^ {0,2}(?:#{1,4}\s+(\S.*)|([A-Z][\w /]{0,30}):\s)", re.MULTILINE)
# Signals a requested output shape. `table`/`columns`/`tabular` bare words
# were removed here (code review 2026-09-24): they matched any mention of
# a table/column, e.g. "the users table has a race condition" or "the CSS
# layout columns", not a request for tabular output. The narrower
# phrases below (`markdown table`, `table with`, `as a table`, and
# `return a table`/`return as table` via the `return ...` alternative)
# cover the real "wants tabular output" cases without that blast radius.
# "list" counts only after an output verb (2026-09-24, real corpus): bare
# "list of" matched "Do you have the list of features outstanding?" and
# "what's next on the list", about half of its hits, and pushed those
# prompts into the >=70 "good prompt" streak band.
_STRUCTURED = re.compile(
    r"\b(json|yaml|csv|markdown table|table with|as a table|bullet(?:ed)? list"
    r"|numbered list|schema|output format|format:"
    r"|(?:give|get|provide|pull\s+out|create|make|produce|print|show|send|compile"
    r"|draft|generate|write|propose|suggest)\b[^.?!\n]{0,30}?\blist"
    r"|return\s+(?:only|a|as)\s+(?:json|yaml|csv|table|checklist|list|summary)"
    r"|respond with only"
    r"|report\s+(?:back\s+)?(?:pass|fail|pass/fail|count|result)s?\b)\b",
    re.IGNORECASE,
)


def estimate_tokens(text: str) -> float:
    return len(text) / 4.0


@lru_cache(maxsize=2048)
def authored_text(text: str) -> str:
    """The part of a prompt someone actually typed: `text` minus fenced
    blocks, pasted terminal sessions and chat logs, recognisable
    machine-output lines, and key-value dumps.

    Every structure signal below (examples, constraints, output format,
    sections) is read from this, not the raw text, so pasted material can't
    earn a structure bonus it didn't author -- a pasted traceback, config
    file or `systemctl status` used to score as "has constraints" or "has
    sections" purely from its own formatting (2026-09-24). Cached because
    each signal calls it for the same prompt."""
    lines = _FENCE.sub("\n", text).splitlines()
    keep = [not _OUTPUT_LINE.match(line) for line in lines]
    # A pasted terminal session or chat log: everything from the first
    # marker to the last, plus the block straight after the last one (its
    # output or message) up to a blank line. Typed text before the session,
    # and a typed question after a blank line, stay authored.
    markers = [i for i, line in enumerate(lines) if _SESSION_MARKER.match(line)]
    if markers:
        end = markers[-1] + 1
        while end < len(lines) and lines[end].strip():
            end += 1
        keep[markers[0] : end] = [False] * (end - markers[0])
    # A traceback frame line is followed by an echo of the source line it
    # points at (`    foo()`), which on its own looks like any indented text.
    for i in range(1, len(lines)):
        if _TB_FRAME.match(lines[i - 1]):
            keep[i] = False
    i = 0
    while i < len(lines):
        j = i
        while j < len(lines) and _KV_LINE.match(lines[j]):
            j += 1
        if j - i >= _KV_RUN:
            keep[i:j] = [False] * (j - i)
        i = max(j, i + 1)
    return "\n".join(line for line, k in zip(lines, keep, strict=True) if k)


def _nonspace(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


def is_bare_paste(text: str) -> bool:
    """Mostly pasted material (code, logs, command output) with little or
    no instruction around it. The original bug (2026-09-23): a bare fenced
    paste with no ask read as a *good* prompt, because the fence itself
    counted as a worked example."""
    total = _nonspace(text)
    if total < _MIN_PASTE_CHARS:
        return False
    authored = authored_text(text)
    return (
        _nonspace(authored) <= _PASTE_AUTHORED_SHARE * total
        and len(authored.split()) < _PASTE_MAX_WORDS
    )


def has_example(text: str) -> bool:
    if _EXAMPLE.search(authored_text(text)):
        return True
    # A fenced block is a worked example only when authored text right
    # before it says so ("expected output like this:"); otherwise it's
    # pasted input and earns nothing.
    return any(
        _EXAMPLE_CUE.search(text[max(0, m.start() - _CUE_WINDOW) : m.start()])
        for m in _FENCE.finditer(text)
    )


def has_constraints(text: str) -> bool:
    authored = authored_text(text)
    return bool(_CONSTRAINT.search(authored)) or bool(_IMPLICIT_CONSTRAINT.search(authored))


def has_structured_output(text: str) -> bool:
    return bool(_STRUCTURED.search(authored_text(text)))


def has_sections(text: str) -> bool:
    """2+ section labels in the authored text = a prompt organised into
    sections. Deliberately excludes bare list items (LIST_ITEM): two plain
    bullets aren't "sectioned", and has_constraints' implicit-list branch
    already credits lists.

    Three shape rules separate typed sections from pasted documents (forms,
    contracts, help text, logs), calibrated on the real corpus 2026-09-24,
    where about 18 of the 26 prompts this used to credit were pastes:
    - any label repeated -> records or a log, not sections ("By:" x6,
      "TIMING:" x3, ipconfig adapters);
    - a "Label:" counts only as a header with a body under it (empty value,
      then non-label text before the next label) or with a prose value of
      _SECTION_MIN_VALUE_WORDS+ words; 1-3 word values are form fields
      ("CMDKEY: x", "Count: 5"). Markdown headings need a body too;
    - at least _SECTION_MIN_DENSITY counted labels per 1,000 chars of the
      whole prompt, so a few labels buried in a pasted document (a 22,700
      char contract with 3) don't make the prompt "sectioned". Typed
      sectioned prompts in the corpus all sat at 0.67 or above."""
    authored = authored_text(text)
    matches = list(_SECTION_NAME.finditer(authored))
    seen: list[str] = []
    counted: set[str] = set()
    for i, match in enumerate(matches):
        heading, label = match.group(1), match.group(2)
        name = (heading or label).strip().lower()
        seen.append(name)
        # Read from the label's own line: the pattern's trailing `\s` can
        # consume a newline, so match.end() may already sit on the next line
        # ("Fix:\nClear it" would read "Clear it" as the value).
        after = match.start(1) + len(heading) if heading else match.start(2) + len(label) + 1
        line_end = authored.find("\n", after)
        line_end = line_end if line_end >= 0 else len(authored)
        words = 0 if heading else len(authored[after:line_end].split())
        if words >= _SECTION_MIN_VALUE_WORDS:
            counted.add(name)
        elif words == 0:
            # A header counts only if something sits under it before the next
            # label: pasted ipconfig leaves adapter headers with nothing below
            # once their dotted detail lines are stripped as output.
            next_start = matches[i + 1].start() if i + 1 < len(matches) else len(authored)
            if authored[line_end:next_start].strip():
                counted.add(name)
    if len(seen) != len(set(seen)):
        return False
    return len(counted) >= 2 and len(counted) * 1000 >= _SECTION_MIN_DENSITY * len(text)


def _segment(prompts: Sequence[Prompt]) -> SegmentMetrics | None:
    if not prompts:
        return None
    token_counts = [estimate_tokens(p.content) for p in prompts]

    sessions: dict[str, list[Prompt]] = {}
    for p in prompts:
        sessions.setdefault(f"{p.source}:{p.session_id}", []).append(p)
    for sess in sessions.values():
        sess.sort(key=lambda p: p.timestamp)

    multi = sum(1 for sess in sessions.values() if len(sess) > 1)
    first_ratios = [
        len(sess[0].content) / total
        for sess in sessions.values()
        if (total := sum(len(p.content) for p in sess)) > 0
    ]

    n = len(prompts)
    return SegmentMetrics(
        prompt_count=n,
        session_count=len(sessions),
        avg_prompt_tokens=sum(token_counts) / n,
        median_prompt_tokens=statistics.median(token_counts),
        prompts_per_session=n / len(sessions),
        refinement_rate=multi / len(sessions),
        example_rate=sum(1 for p in prompts if has_example(p.content)) / n,
        constraint_rate=sum(1 for p in prompts if has_constraints(p.content)) / n,
        structured_output_rate=sum(1 for p in prompts if has_structured_output(p.content)) / n,
        avg_first_message_ratio=(sum(first_ratios) / len(first_ratios) if first_ratios else 0.0),
    )


def compute_metrics(prompts: Sequence[Prompt]) -> StyleMetrics:
    return StyleMetrics(
        human=_segment([p for p in prompts if p.origin is PromptOrigin.HUMAN]),
        machine=_segment([p for p in prompts if p.origin is PromptOrigin.MACHINE]),
        overall=_segment(list(prompts)),
    )
