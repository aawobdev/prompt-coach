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

Also handles Hermes's `pre_llm_call` shell hook (2026-07-30): Hermes's own
docs describe this event as "Claude Code's UserPromptSubmit equivalent"
and its shell hooks accept the same JSON wire protocol -- but the payload
shape differs (the user's message is at `extra.user_message`, not a
top-level `prompt`), and critically `pre_llm_call` can only inject
context, it cannot block. There's no way to hold a message back and offer
a rewrite the way Claude Code's UserPromptSubmit can, so the Hermes path
is deterministic tip-only (same calibrated triggers, same once-per-session
gate) regardless of nudge mode -- "off" still disables it entirely, but
"always" has no meaningful translation here (there's nothing to
"always block and rewrite"), so it collapses to the same coach-style
behavior as "coach".
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_coach.analysis.docs import find_project_docs, is_redirect_stub
from prompt_coach.analysis.metrics import has_constraints, has_example, has_structured_output
from prompt_coach.config import Config
from prompt_coach.models import PromptOrigin
from prompt_coach.stores.base import classify_origin
from prompt_coach.stores.claude_code import parse_line

if TYPE_CHECKING:  # never imported at runtime -- see the note below
    from prompt_coach.llm.client import LocalLLM

# llm.client is NOT imported at module scope: it pulls in the `openai` SDK,
# whose own type surface (Assistants/Threads/Batches/Evals APIs we never
# use) costs ~700-900ms to import alone (measured 2026-07-29). This module
# runs synchronously on every Claude Code prompt submission (see the module
# docstring), so paying that tax when mode is "off" or no trigger fired --
# the common case -- would be a real, constant per-prompt delay for no
# benefit. Imported lazily in rewrite_prompt()/_make_llm() instead, so it's
# only paid on the turns that actually call the LLM.

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
    "the agent starts."  # platform-neutral: this hook now also fires for Hermes (2026-07-30)
)

# Calibrated against the real corpus (2026-07-27 spike), not guessed: these
# four words/phrases are exactly what fired across 26 short (<200 char)
# prompts referencing sweeping, unconstrained scope out of 1,739 human
# prompts (1.5%). Guessed synonyms -- redesign/rewrite/refactor/overhaul/
# restructure -- never appeared once in that corpus, so they're left out
# rather than added speculatively.
_BROAD_SCOPE = re.compile(r"\b(everything|all of|whole|revamp)\b", re.IGNORECASE)

# Harness-injected payloads arrive through UserPromptSubmit shaped exactly
# like typed prompts, but nobody typed them. Seen live 2026-07-31: nudge
# blocked a <task-notification> (a background-task completion event) and
# offered to "tighten" it. Match the wrapper tags Claude Code injects as
# user turns; classify_origin then catches the orchestration task specs
# (TASK:/HANDOFF: one-shots) on top.
_HARNESS_WRAPPED = re.compile(
    r"^\s*<(task-notification|command-message|command-name|system-reminder|"
    r"local-command-stdout|bash-(input|stdout|stderr))\b"
)


def _is_machine_prompt(prompt: str) -> bool:
    return bool(_HARNESS_WRAPPED.match(prompt)) or classify_origin(prompt) is PromptOrigin.MACHINE


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
    if _is_machine_prompt(prompt):
        return None
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
    "is broad and open-ended. When a SESSION CONTEXT block is provided "
    "(working directory, project docs, earlier prompts), ground the rewrite "
    "in it: use the project's real names, paths, and terminology instead of "
    "generic placeholders, and read ambiguous references against what the "
    "session has been about. The context is background for YOU, not content "
    "for the rewrite: never recite, summarize, or restate the project docs "
    "or conventions -- the assistant reading the prompt already has them. "
    "Only include a specific name or path where the task itself needs it. "
    "Keep the rewrite about as long as a well-written version of the "
    "original ask (a few sentences), never a mini-spec. Preserve the "
    "original intent exactly: don't invent requirements the user didn't "
    "ask for, don't answer the prompt, just rewrite it. Respond with JSON "
    'only: {"rewritten_prompt": "..."}'
)

# Caps for the context block fed to the rewrite LLM. Generous enough to be
# useful, small enough that the hook's inline LLM call stays well inside the
# default model's context window even with a long prompt.
_CTX_DOC_CHARS = 1500
_CTX_PRIOR_PROMPTS = 3
_CTX_PROMPT_CHARS = 300


def gather_context(payload: dict, home: Path | None = None) -> str | None:
    """Assemble what we know about where the prompt is running: the working
    directory, an excerpt of the nearest project doc (CLAUDE.md/AGENTS.md/
    README.md, same walk-up as the docs-quality analysis), and the last few
    human prompts from the session transcript. A rewrite with none of this
    can only ever be generic; the whole value of rewriting in a hook is that
    the surrounding context is sitting right there in the payload."""
    parts: list[str] = []
    cwd = payload.get("cwd")
    if cwd:
        parts.append(f"Working directory: {cwd}")
        for doc_path in find_project_docs(cwd, home=home):
            try:
                text = doc_path.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                continue
            if not text or is_redirect_stub(text):
                continue
            parts.append(f"Project doc ({doc_path.name}, excerpt):\n{text[:_CTX_DOC_CHARS]}")
            break
    transcript = payload.get("transcript_path")
    if transcript:
        current = payload.get("prompt", "").strip()
        prior = [
            p
            for p in _tail_human_prompts(Path(transcript), _CTX_PRIOR_PROMPTS + 1)
            if p.strip() != current
        ][:_CTX_PRIOR_PROMPTS]
        if prior:
            lines = "\n".join(f"- {p[:_CTX_PROMPT_CHARS]}" for p in prior)
            parts.append(f"Earlier prompts this session (newest first):\n{lines}")
    return "\n\n".join(parts) if parts else None


def rewrite_prompt(prompt: str, llm: LocalLLM, context: str | None = None) -> str | None:
    """Ask the local LLM for an improved rewrite, grounded in `context`
    (see gather_context) when available. Returns None on any failure
    (unreachable, malformed output) -- callers must have a non-LLM
    fallback; nudge must never leave a prompt stuck with no way forward."""
    from prompt_coach.llm.client import LLMUnavailable

    user = prompt
    if context:
        user = f"SESSION CONTEXT:\n{context}\n\nPROMPT TO REWRITE:\n{prompt}"
    try:
        result = llm.complete_json(_REWRITE_SYSTEM, user, max_tokens=800)
    except LLMUnavailable:
        return None
    rewritten = result.get("rewritten_prompt")
    return rewritten.strip() if isinstance(rewritten, str) and rewritten.strip() else None


def _make_llm(cfg: Config) -> LocalLLM | None:
    """LocalLLM bounded by nudge's own (short) timeout, not report/query's
    120s default -- this runs inline in a hook, not a background job."""
    from prompt_coach.llm.client import LocalLLM, RemoteEndpointRefused

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


def _rewrite_or_fallback(
    prompt: str,
    llm: LocalLLM | None,
    fallback_message: str | None,
    context: str | None = None,
) -> dict:
    rewritten = rewrite_prompt(prompt, llm, context=context) if llm is not None else None
    if rewritten is None:
        return {"systemMessage": fallback_message} if fallback_message else {}
    return {"decision": "block", "reason": _block_reason(rewritten)}


def _resolve_mode(cfg: Config, cwd: str | None) -> str:
    """Claude Code's hook settings can't override a globally-registered hook
    per project -- hooks merge across scopes, they don't replace (checked
    live, DECISIONS.md 2026-07-30) -- so per-directory control lives here
    instead, keyed off the hook payload's own `cwd`. The longest matching
    path prefix in `cfg.nudge.dir_overrides` wins, so a nested override can
    be more specific than a parent directory; falls back to the global mode."""
    if not cwd or not cfg.nudge.dir_overrides:
        return cfg.nudge.mode
    best_match: str | None = None
    best_len = -1
    for override_path, mode in cfg.nudge.dir_overrides.items():
        normalized = override_path.rstrip("/")
        if (cwd == normalized or cwd.startswith(normalized + "/")) and len(normalized) > best_len:
            best_len = len(normalized)
            best_match = mode
    return best_match if best_match is not None else cfg.nudge.mode


def _hermes_tip_response(payload: dict, cfg: Config) -> dict:
    """Hermes's `pre_llm_call` shell hook: same JSON wire protocol as Claude
    Code's UserPromptSubmit (Hermes's own docs call this event the direct
    equivalent), but the message lives at `extra.user_message`, and
    `pre_llm_call` can only inject `{"context": ...}` -- it cannot block, so
    there's no rewrite-and-hold-back flow here. Deterministic tip only,
    regardless of nudge mode (see module docstring)."""
    mode = _resolve_mode(cfg, payload.get("cwd"))
    if mode == "off":
        return {}
    session_id = payload.get("session_id", "")
    if not session_id:
        return {}
    prompt = (payload.get("extra") or {}).get("user_message", "")
    if not prompt:
        return {}
    tip = evaluate(prompt, session_id, cfg.cache_dir)
    return {"context": tip} if tip else {}


def build_response(payload: dict, cfg: Config) -> dict:
    """The one function the CLI calls: dispatches on `hook_event_name` and
    the resolved nudge mode (global `cfg.nudge.mode`, or a per-directory
    override -- see `_resolve_mode`). Handles Claude Code's UserPromptSubmit
    (which can block-and-rewrite) and Stop (tip-only, coach mode only), plus
    Hermes's `pre_llm_call` (tip-only always -- see `_hermes_tip_response`)."""
    if payload.get("hook_event_name") == "pre_llm_call":
        return _hermes_tip_response(payload, cfg)

    mode = _resolve_mode(cfg, payload.get("cwd"))
    if mode == "off":
        return {}
    session_id = payload.get("session_id", "")
    if not session_id:
        return {}

    if payload.get("hook_event_name") == "Stop":
        if mode != "coach":
            return {}
        transcript = payload.get("transcript_path")
        return hook_response_stop(session_id, Path(transcript), cfg.cache_dir) if transcript else {}

    prompt = payload.get("prompt", "")
    if not prompt:
        return {}

    if mode == "always":
        if _is_machine_prompt(prompt):
            return {}  # harness traffic, not a prompt anyone typed
        llm = _make_llm(cfg)
        context = gather_context(payload) if llm is not None else None
        return _rewrite_or_fallback(prompt, llm, fallback_message=None, context=context)

    # "coach": only the calibrated triggers, once per session.
    tip = _tip_for(prompt)
    if tip is None or session_id in _load_nudged_sessions(cfg.cache_dir):
        return {}
    _record_nudge(cfg.cache_dir, session_id)
    llm = _make_llm(cfg)
    context = gather_context(payload) if llm is not None else None
    return _rewrite_or_fallback(prompt, llm, fallback_message=tip, context=context)


def _tail_human_prompts(transcript_path: Path, limit: int) -> list[str]:
    """Up to `limit` most recent human prompts, newest first, by tailing the
    transcript JSONL from EOF and reusing the same acceptance filters as the
    real corpus reader (parse_line), rather than inventing a second parser.
    Tails in growing chunks instead of reading the whole file: transcripts
    run to hundreds of MB and this must stay near-instant."""
    try:
        size = transcript_path.stat().st_size
    except OSError:
        return []
    found: list[str] = []
    with open(transcript_path, "rb") as f:
        for chunk_n in range(1, _TAIL_MAX_CHUNKS + 1):
            start = max(0, size - _TAIL_CHUNK * chunk_n)
            f.seek(start)
            data = f.read()
            found.clear()  # rescan: the bigger window re-covers the smaller one
            for line in reversed(data.decode("utf-8", errors="replace").splitlines()):
                prompt = parse_line(line)
                if prompt is not None and prompt.origin is PromptOrigin.HUMAN:
                    found.append(prompt.content)
                    if len(found) >= limit:
                        return found
            if start == 0:
                break
    return found


def _last_human_prompt(transcript_path: Path) -> str | None:
    """Stop's hook input carries no prompt text -- only session_id and
    transcript_path -- so recover the just-submitted prompt from the
    transcript tail."""
    prompts = _tail_human_prompts(transcript_path, 1)
    return prompts[0] if prompts else None


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
