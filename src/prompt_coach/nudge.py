"""Live nudge: a Claude Code hook with three modes (see config.py NudgeConfig):

- "coach" (default): the calibrated weak-prompt triggers fire at most once
  per session. When the local LLM is reachable, the prompt is BLOCKED and an
  LLM-rewritten version is offered in the block reason -- you paste it in
  yourself if you want it, nothing auto-resubmits. When the LLM isn't
  reachable, it degrades to the old non-blocking systemMessage tip.
- "always": every UserPromptSubmit is blocked and rewritten, regardless of
  quality or session history -- an explicit opt-in, since it adds LLM
  latency to every prompt. If the LLM is unreachable, the prompt is let
  through unmodified (blocking with no way out would be worse than skipping).
- "off": nudge never fires.

Only UserPromptSubmit can block (Claude Code has no equivalent for Stop --
`decision: block` there means "keep going," not "don't send" -- so it would
mean something different, not the same thing later). Stop keeps the old
tip-only behavior, and only in "coach" mode: in "always" mode every prompt
is already caught pre-submission, so there's nothing left for Stop to catch
after the fact.

Calibrated against the real corpus (see DECISIONS.md): 77-97% of substantial
human prompts already fail the A5/A6 rubric rules, so firing on every rule
failure would nag on nearly every message. Coach mode instead fires for two
calibrated patterns: a long prompt (>=200 chars) missing output shape
(A5/A6), or a short prompt with an unconstrained broad-scope word
("everything"/"all of"/"whole"/"revamp" -- exactly what fired in the real
corpus, not a guessed list). UserPromptSubmit and Stop share the same
once-per-session gate in coach mode, so whichever moment catches a weak
prompt first is the only one that speaks.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from prompt_coach.analysis.metrics import has_constraints, has_example, has_structured_output
from prompt_coach.config import Config
from prompt_coach.llm.client import LLMUnavailable, LocalLLM, RemoteEndpointRefused
from prompt_coach.models import PromptOrigin
from prompt_coach.stores.claude_code import parse_line

_MIN_PROMPT_CHARS = 200  # matches rubric.py's A4 structure threshold
_MAX_TRACKED_SESSIONS = 500  # bound the state file; oldest entries dropped
_TAIL_CHUNK = 65536  # bytes; last message is almost always within one tail window
_TAIL_MAX_CHUNKS = 4  # give up after ~256KB scanned backwards from EOF

_TIP_SHAPE = (
    "prompt-coach tip: no output format or worked example in that prompt -- "
    "consider stating the shape you want back, or giving a short example."
)
_TIP_SCOPE = (
    "prompt-coach tip: that's a big, open-ended ask with nothing scoping it -- "
    "consider naming what's in scope (or explicitly out of scope) before "
    "Claude starts."
)

# Calibrated against the real corpus (2026-07-27 spike), not guessed: these
# four words/phrases are exactly what fired across 26 short (<200 char)
# prompts referencing sweeping, unconstrained scope out of 1,739 human
# prompts (1.5%). Guessed synonyms -- redesign/rewrite/refactor/overhaul/
# restructure -- never appeared once in that corpus, so they're left out
# rather than added speculatively.
_BROAD_SCOPE = re.compile(r"\b(everything|all of|whole|revamp)\b", re.IGNORECASE)


def _is_long_unstructured(prompt: str) -> bool:
    return len(prompt) >= _MIN_PROMPT_CHARS and not (
        has_example(prompt) or has_structured_output(prompt)
    )


def _is_short_and_vague(prompt: str) -> bool:
    return (
        len(prompt) < _MIN_PROMPT_CHARS
        and bool(_BROAD_SCOPE.search(prompt))
        and not has_constraints(prompt)
    )


def _tip_for(prompt: str) -> str | None:
    if _is_long_unstructured(prompt):
        return _TIP_SHAPE
    if _is_short_and_vague(prompt):
        return _TIP_SCOPE
    return None


def _state_path(cache_dir: Path) -> Path:
    return cache_dir / "nudge_state.json"


def _load_nudged_sessions(cache_dir: Path) -> list[str]:
    path = _state_path(cache_dir)
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    sessions = data.get("nudged_sessions", [])
    return sessions if isinstance(sessions, list) else []


def _record_nudge(cache_dir: Path, session_id: str) -> None:
    sessions = _load_nudged_sessions(cache_dir)
    sessions.append(session_id)
    sessions = sessions[-_MAX_TRACKED_SESSIONS:]
    cache_dir.mkdir(parents=True, exist_ok=True)
    _state_path(cache_dir).write_text(json.dumps({"nudged_sessions": sessions}))


def should_nudge(prompt: str, session_id: str, cache_dir: Path) -> bool:
    if _tip_for(prompt) is None:
        return False
    return session_id not in _load_nudged_sessions(cache_dir)


def evaluate(prompt: str, session_id: str, cache_dir: Path) -> str | None:
    """Return a tip message if this prompt/session should be nudged, and
    record the session as nudged so it fires at most once each."""
    tip = _tip_for(prompt)
    if tip is None or session_id in _load_nudged_sessions(cache_dir):
        return None
    _record_nudge(cache_dir, session_id)
    return tip


_REWRITE_SYSTEM = (
    "You rewrite a user's prompt to a coding assistant so it is clearer, "
    "more specific, and better structured -- state an output format or add "
    "a short example if one is missing, name scope/constraints if the ask "
    "is broad and open-ended. Preserve the original intent exactly: don't "
    "invent requirements the user didn't ask for, don't answer the prompt, "
    'just rewrite it. Respond with JSON only: {"rewritten_prompt": "..."}'
)


def rewrite_prompt(prompt: str, llm: LocalLLM) -> str | None:
    """Ask the local LLM for an improved rewrite. Returns None on any
    failure (unreachable, malformed output) -- callers must have a
    non-LLM fallback; nudge must never leave a prompt stuck with no way
    forward."""
    try:
        result = llm.complete_json(_REWRITE_SYSTEM, prompt, max_tokens=800)
    except LLMUnavailable:
        return None
    rewritten = result.get("rewritten_prompt")
    return rewritten.strip() if isinstance(rewritten, str) and rewritten.strip() else None


def _make_llm(cfg: Config) -> LocalLLM | None:
    """LocalLLM bounded by nudge's own (short) timeout, not report/query's
    120s default -- this runs inline in a hook, not a background job."""
    try:
        llm = LocalLLM(
            cfg.llm.base_url,
            cfg.llm.model,
            api_key=cfg.llm.api_key,
            timeout=cfg.nudge.llm_timeout,
            allow_remote=cfg.llm.allow_remote,
        )
    except RemoteEndpointRefused:
        return None
    return llm if llm.available() else None


def _block_reason(rewritten: str) -> str:
    return (
        "prompt-coach held this prompt back -- here's a tighter rewrite:\n\n"
        f"{rewritten}\n\n"
        "Paste that in if you want it, or resend your original as-is."
    )


def _rewrite_or_fallback(prompt: str, llm: LocalLLM | None, fallback_message: str | None) -> dict:
    rewritten = rewrite_prompt(prompt, llm) if llm is not None else None
    if rewritten is None:
        return {"systemMessage": fallback_message} if fallback_message else {}
    return {"decision": "block", "reason": _block_reason(rewritten)}


def build_response(payload: dict, cfg: Config) -> dict:
    """The one function the CLI calls: dispatches on `hook_event_name` and
    `cfg.nudge.mode`. Handles UserPromptSubmit (which can block-and-rewrite)
    and Stop (tip-only, coach mode only -- see module docstring)."""
    if cfg.nudge.mode == "off":
        return {}
    session_id = payload.get("session_id", "")
    if not session_id:
        return {}

    if payload.get("hook_event_name") == "Stop":
        if cfg.nudge.mode != "coach":
            return {}
        transcript = payload.get("transcript_path")
        return hook_response_stop(session_id, Path(transcript), cfg.cache_dir) if transcript else {}

    prompt = payload.get("prompt", "")
    if not prompt:
        return {}

    if cfg.nudge.mode == "always":
        return _rewrite_or_fallback(prompt, _make_llm(cfg), fallback_message=None)

    # "coach": only the calibrated triggers, once per session.
    tip = _tip_for(prompt)
    if tip is None or session_id in _load_nudged_sessions(cfg.cache_dir):
        return {}
    _record_nudge(cfg.cache_dir, session_id)
    return _rewrite_or_fallback(prompt, _make_llm(cfg), fallback_message=tip)


def _last_human_prompt(transcript_path: Path) -> str | None:
    """Stop's hook input carries no prompt text -- only session_id and
    transcript_path -- so recover the just-submitted prompt by tailing the
    transcript JSONL from EOF and reusing the same acceptance filters as the
    real corpus reader (parse_line), rather than inventing a second parser.
    Tails in growing chunks instead of reading the whole file: transcripts
    run to hundreds of MB and this must stay near-instant."""
    try:
        size = transcript_path.stat().st_size
    except OSError:
        return None
    with open(transcript_path, "rb") as f:
        for chunk_n in range(1, _TAIL_MAX_CHUNKS + 1):
            start = max(0, size - _TAIL_CHUNK * chunk_n)
            f.seek(start)
            data = f.read()
            for line in reversed(data.decode("utf-8", errors="replace").splitlines()):
                prompt = parse_line(line)
                if prompt is not None and prompt.origin is PromptOrigin.HUMAN:
                    return prompt.content
            if start == 0:
                break
    return None


def hook_response_stop(session_id: str, transcript_path: Path, cache_dir: Path) -> dict:
    """Build the Stop hook JSON response: always the plain non-blocking
    `systemMessage` tip (Stop can't block-and-offer-a-rewrite the way
    UserPromptSubmit can -- see module docstring), reading the prompt from
    the transcript since Stop's stdin carries no prompt text."""
    prompt = _last_human_prompt(transcript_path)
    if prompt is None:
        return {}
    tip = evaluate(prompt, session_id, cache_dir)
    return {"systemMessage": tip} if tip else {}
