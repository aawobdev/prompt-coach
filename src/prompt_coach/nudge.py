"""Live nudge: a Claude Code hook with three modes (see config.py NudgeConfig):

- "coach" (default): the calibrated weak-prompt triggers fire at most once
  per session. When the local LLM is reachable, the prompt is BLOCKED and an
  LLM-rewritten version is offered in the block reason -- run
  /prompt-coach:accept to resend the rewrite or /prompt-coach:original to
  resend your prompt as-is (see consume_pending_rewrite below; these are
  skills in the Claude Code plugin under plugin/, not part of this Python
  package). When the LLM isn't reachable, it degrades to the old
  non-blocking systemMessage tip.
- "auto": the same calibrated triggers as coach decide whether a prompt
  needs work, but the improvement is applied automatically -- the prompt
  is NOT blocked and there is no accept/reject step. UserPromptSubmit
  hooks cannot replace the prompt text (only block it or add context --
  checked against the hooks docs 2026-08-10), and the session model
  already has the full repo and conversation in context -- strictly more
  than gather_context can scrape together, from a strictly stronger
  model than the local rewriter. So auto mode calls no LLM at all: it
  injects an `additionalContext` directive telling the session model
  itself to refine the prompt (scope, output shape, tables/visuals for
  list-shaped answers) and answer the refined version, opening its reply
  with a one-line "Refined:" statement so the user sees what it ran
  with. Near-zero latency, no dependency on the local LLM being up. No
  once-per-session gate: auto never interrupts, so there is no nag cost
  to ration -- every triggered prompt gets improved.
- "always": every UserPromptSubmit is blocked and rewritten, regardless of
  quality or session history -- an explicit opt-in, since it adds LLM
  latency to every single prompt. If the LLM is unreachable, the prompt is
  let through unmodified (blocking with no way out would be worse than skipping).
- "off": nudge never fires.

Only UserPromptSubmit can block (Claude Code has no equivalent for Stop --
`decision: block` there means "keep going," not "don't send" -- so it would
mean something different, not the same thing later). Stop keeps the old
tip-only behavior, and only in "coach" mode: in "auto" and "always" modes
every weak prompt is already caught pre-submission, so there's nothing left
for Stop to catch after the fact.

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
behavior as "coach". "auto" collapses the same way for now: pre_llm_call's
context injection could in principle carry an auto-rewrite, but that path
stays deterministic-only until the inline-LLM latency is proven acceptable
inside Hermes's hook budget.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from prompt_coach.analysis.docs import find_project_docs, is_redirect_stub
from prompt_coach.analysis.metrics import (
    has_constraints,
    has_example,
    has_structured_output,
    is_bare_paste,
)
from prompt_coach.cache import CacheDB
from prompt_coach.config import Config
from prompt_coach.models import PromptOrigin
from prompt_coach.stores.base import classify_origin, is_harness_wrapped
from prompt_coach.stores.claude_code import parse_line
from prompt_coach.streaks import record_prompt

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
_TIP_PASTE = (
    "prompt-coach tip: that's mostly pasted material with little instruction -- "
    "say what you want done with it (fix, explain, review?) and what you've "
    "already tried."
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


def _is_machine_prompt(prompt: str) -> bool:
    # is_harness_wrapped (stores/base.py) is the single source of truth for
    # this pattern -- code review 2026-09-24 found the Claude Code store
    # needed the exact same check (it didn't have one at all), so this
    # module's former private copy moved there rather than staying
    # duplicated in two places.
    return bool(is_harness_wrapped(prompt)) or classify_origin(prompt) is PromptOrigin.MACHINE


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
    # Before the shape check: a bare paste used to read as well-structured
    # (its own fence/formatting earned the example and structure signals),
    # so this is the one trigger that catches it (2026-09-24).
    if is_bare_paste(prompt):
        return _TIP_PASTE
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
    "is broad and open-ended. If the answer would naturally be a list or "
    "enumeration (options, comparisons, findings, inventories), have the "
    "rewrite ask for it as a table -- or a visual (chart/diagram) when the "
    "items have relationships or proportions -- rather than prose bullets. "
    "First decide what the prompt is about. If it is a general-knowledge or "
    "everyday question (not about this codebase, its files, or the session's "
    "work), rewrite it as a general question and ignore the session context "
    "entirely -- never reinterpret an everyday word as a project term or send "
    "the assistant searching the working directory for it. A question that "
    "names this project's services, accounts, data, or code is project work "
    "even if phrased generally. Otherwise, "
    "when a SESSION CONTEXT block is provided "
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
    from prompt_coach.config import APIKeyCommandFailed
    from prompt_coach.llm.client import LocalLLM, RemoteEndpointRefused

    if not cfg.llm.configured:
        return None
    try:
        llm = LocalLLM(
            cfg.llm.base_url,
            cfg.llm.model,
            api_key=cfg.llm.resolve_api_key(),
            timeout=cfg.nudge.llm_timeout,
            allow_remote=cfg.llm.allow_remote,
            extra_body=cfg.llm.extra_body,
        )
    except (RemoteEndpointRefused, APIKeyCommandFailed):
        return None
    return llm if llm.available() else None


# Plugin skills are always namespaced /<plugin>:<skill> (verified 2026-09-20),
# so these track plugin/skills/{accept,original}; tests/test_plugin.py checks
# they stay in sync.
ACCEPT_COMMAND = "/prompt-coach:accept"
ORIGINAL_COMMAND = "/prompt-coach:original"


def _block_reason(rewritten: str) -> str:
    return (
        "prompt-coach held this prompt back -- here's a tighter rewrite:\n\n"
        f"{rewritten}\n\n"
        "Reply y to send the rewrite, n to send your original "
        f"(or run {ACCEPT_COMMAND} / {ORIGINAL_COMMAND})."
    )


# One-letter answers to a block (2026-09-27): hooks can't render a button or
# replace the prompt text, so `y`/`n` go through as-is and the chosen text
# rides along as additionalContext -- the same channel auto mode uses.
_REPLY_WANTS = {"y": "rewritten", "n": "original"}
# With nothing pending these are answers to Claude's own questions; always
# mode used to block and "rewrite" them into nonsense (found live 2026-09-27).
_BARE_CONFIRMATIONS = frozenset({"y", "n", "yes", "no", "ok", "okay"})
_REPLY_HEADINGS = {
    "rewritten": "Sent (prompt-coach rewrite):",
    "original": "Sent (your original prompt):",
}


def _reply_response(text: str, want: str) -> dict:
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "prompt-coach: the user's one-letter message is their answer to "
                "prompt-coach's offer, not a request in itself. Their actual "
                "request is the text below -- respond to it directly. They can't "
                "see that text in the chat, so begin your reply with a "
                f"blockquote showing it verbatim, headed `**{_REPLY_HEADINGS[want]}**`, "
                f"then answer it below.\n\n{text}"
            ),
        }
    }


def _answer_pending(cache_dir: Path, session_id: str, prompt: str) -> dict | None:
    """Handle a prompt sent while a block's rewrite is pending: `y`/`n`
    deliver one side of it; any other non-slash prompt drops it, so a later
    `y` answering one of Claude's own questions can't resurrect a stale
    offer. Slash commands leave it alone -- /prompt-coach:accept consumes it
    itself. Returns None when the prompt should be handled normally."""
    data = _load_pending_state(cache_dir)
    if session_id not in data["pending"]:
        return None
    want = _REPLY_WANTS.get(prompt.strip().lower())
    if want is None:
        if not prompt.lstrip().startswith("/"):
            del data["pending"][session_id]
            _write_pending_state(cache_dir, data)
        return None
    entry = data["pending"].pop(session_id)
    _write_pending_state(cache_dir, data)
    return _reply_response(entry.get(want, ""), want)


# Auto mode's whole trick: UserPromptSubmit can't replace the prompt text
# (only block, or add context -- hooks docs, 2026-08-10), and the session
# model already holds the full repo/conversation context -- strictly more
# than gather_context's excerpts -- and is strictly stronger than the local
# rewrite model. So auto mode calls no LLM at all: it injects a directive
# telling the session model itself to refine the prompt before answering
# (2026-08-10, second pass -- the first cut called the local LLM here and
# its rewrites were both slower and worse-grounded than this).
_AUTO_RESPONSE = {
    "hookSpecificOutput": {
        "hookEventName": "UserPromptSubmit",
        "additionalContext": (
            "prompt-coach auto-improve: the user has automatic prompt "
            "improvement enabled, and this prompt tripped a weak-prompt "
            "trigger (vague scope, no output shape, or mostly pasted material "
            "with little instruction). Before answering, "
            "silently rewrite it into the clearer, better-scoped request "
            "the user most plausibly meant, grounded in this project and "
            "conversation: pin down scope and constraints if the ask is "
            "broad; decide the output format if none is stated -- and if "
            "the answer is naturally a list or enumeration, present it as "  # noqa: E501
            "a table, or as a chart/diagram when the items have "
            "relationships or proportions (this user parses tables and "
            "visuals far better than prose bullets). Do not invent "
            "requirements beyond the plausible intent. Begin your reply "
            "by stating the full refined request (prefixed 'Refined:'), "
            "then answer that refined version."
        ),
    },
    "systemMessage": (
        "prompt-coach: weak-prompt trigger fired -- the model has been "
        "asked to refine this prompt before answering (auto mode)."
    ),
}


def _auto_rewrite_response(rewritten: str, model: str) -> dict:
    """Path 1: the rewrite rides along as additionalContext (the hook can't
    replace the prompt text), and the reply opens by quoting it -- the user
    otherwise never sees it."""
    return {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": (
                "prompt-coach auto-improve: the user's prompt tripped a weak-prompt "
                "trigger and has been rewritten. Treat the rewritten request below "
                "as what the user is asking; use their original wording only to "
                "resolve ambiguity. They can't see the rewrite in the chat, so "
                "begin your reply with a blockquote showing it verbatim, headed "
                f"`**{_REPLY_HEADINGS['rewritten']}**`, then answer it below."
                f"\n\n{rewritten}"
            ),
        },
        "systemMessage": f"prompt-coach: rewrote this prompt with {model} (auto mode).",
    }


def _pending_path(cache_dir: Path) -> Path:
    return cache_dir / "nudge_pending.json"


def _load_pending_state(cache_dir: Path) -> dict:
    path = _pending_path(cache_dir)
    default = {"pending": {}, "skip_next": []}
    if not path.is_file():
        return default
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return default
    if not isinstance(data, dict):
        return default
    data.setdefault("pending", {})
    data.setdefault("skip_next", [])
    return data


def _write_pending_state(cache_dir: Path, data: dict) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    _pending_path(cache_dir).write_text(json.dumps(data))


def _save_pending_rewrite(cache_dir: Path, session_id: str, original: str, rewritten: str) -> None:
    """Record what a block offered, so /prompt-coach:accept and
    /prompt-coach:original (see nudge_consume in cli.py) can later resend the right text without
    the user having to copy/paste it themselves."""
    data = _load_pending_state(cache_dir)
    data["pending"][session_id] = {"original": original, "rewritten": rewritten}
    overflow = len(data["pending"]) - _MAX_TRACKED_SESSIONS
    for key in list(data["pending"])[: max(overflow, 0)]:
        del data["pending"][key]
    _write_pending_state(cache_dir, data)


def _peek_pending_rewrite(cache_dir: Path, session_id: str) -> str | None:
    """Read the pending rewrite for a session without consuming it."""
    data = _load_pending_state(cache_dir)
    entry = data["pending"].get(session_id)
    return entry.get("rewritten") if entry else None


def consume_pending_rewrite(cache_dir: Path, session_id: str, want: str) -> str | None:
    """CLI entry point for /prompt-coach:accept and /prompt-coach:original: pop the
    pending rewrite for `session_id` and return `want` ("rewritten" or
    "original"), or None if nothing is pending (stale/reused command).
    Also marks the session to skip the nudge gate on its next
    UserPromptSubmit, so resending the chosen text doesn't immediately get
    held back again."""
    data = _load_pending_state(cache_dir)
    entry = data["pending"].pop(session_id, None)
    if entry is None:
        return None
    if session_id not in data["skip_next"]:
        data["skip_next"].append(session_id)
    _write_pending_state(cache_dir, data)
    return entry.get(want)


def _pop_skip_next(cache_dir: Path, session_id: str) -> bool:
    data = _load_pending_state(cache_dir)
    if session_id not in data["skip_next"]:
        return False
    data["skip_next"].remove(session_id)
    _write_pending_state(cache_dir, data)
    return True


def _rewrite_or_fallback(
    prompt: str,
    llm: LocalLLM | None,
    fallback_message: str | None,
    cache_dir: Path,
    session_id: str,
    context: str | None = None,
) -> dict:
    rewritten = rewrite_prompt(prompt, llm, context=context) if llm is not None else None
    if rewritten is None:
        return {"systemMessage": fallback_message} if fallback_message else {}
    _save_pending_rewrite(cache_dir, session_id, prompt, rewritten)
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


def _record_audit(
    cfg: Config,
    payload: dict,
    original: str,
    trigger: str | None = None,
    refined: str | None = None,
) -> None:
    """Record an audit event for a nudge intervention."""
    try:
        cache = CacheDB(cfg.cache_dir / "cache.db")
        hook = payload.get("hook_event_name", "UserPromptSubmit")
        cache.record_audit_event(
            session_id=payload.get("session_id", ""),
            mode=_resolve_mode(cfg, payload.get("cwd")),
            source=hook,
            hook_event=hook,
            trigger=trigger or "",
            original_prompt=original,
            refined_prompt=refined,
        )
        cache.close()
    except Exception:
        import logging

        logging.getLogger(__name__).warning("audit record failed", exc_info=True)


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
    if tip:
        _record_audit(cfg, payload, prompt, trigger=tip)
    record_prompt(cfg.cache_dir, prompt, was_nudged=tip is not None)
    return {"context": tip} if tip else {}


def build_response(payload: dict, cfg: Config) -> dict:
    """The one function the CLI calls: dispatches on `hook_event_name` and
    the resolved nudge mode (global `cfg.nudge.mode`, or a per-directory
    override -- see `_resolve_mode`). Handles Claude Code's UserPromptSubmit
    (block-and-rewrite in coach/always, LLM-free self-improve directive
    in auto) and Stop
    (tip-only, coach mode only), SessionStart (one-line summary, see
    session_summary.py), plus Hermes's `pre_llm_call` (tip-only always --
    see `_hermes_tip_response`)."""
    if payload.get("hook_event_name") == "pre_llm_call":
        return _hermes_tip_response(payload, cfg)

    mode = _resolve_mode(cfg, payload.get("cwd"))
    if mode == "off":
        return {}
    if payload.get("hook_event_name") == "SessionStart":
        from prompt_coach.session_summary import hook_response_session_start

        return hook_response_session_start(payload, cfg.cache_dir, mode)
    session_id = payload.get("session_id", "")
    if not session_id:
        return {}

    if payload.get("hook_event_name") == "Stop":
        if mode not in ("coach", "hint"):
            return {}
        transcript = payload.get("transcript_path")
        return (
            hook_response_stop(
                session_id,
                Path(transcript),
                cfg.cache_dir,
                cfg=cfg,
                payload=payload,
                hint_mode=(mode == "hint"),
            )
            if transcript
            else {}
        )

    prompt = payload.get("prompt", "")
    if not prompt:
        return {}

    answered = _answer_pending(cfg.cache_dir, session_id, prompt)
    if answered is not None:
        return answered

    if _pop_skip_next(cfg.cache_dir, session_id):
        # This is the resend triggered by /prompt-coach:accept or :original
        # (consume_pending_rewrite set this flag) -- let it through as-is,
        # regardless of mode, rather than immediately holding it back again.
        return {}

    if mode == "auto":
        # Same calibrated triggers as coach (and _tip_for already screens
        # out machine prompts), but no once-per-session gate: auto never
        # interrupts, so there's no nag cost to ration. By default no LLM
        # call either (see _AUTO_RESPONSE); with auto_rewrite the [llm]
        # model rewrites it first, falling back to that directive.
        tip = _tip_for(prompt)
        record_prompt(cfg.cache_dir, prompt, was_nudged=tip is not None)
        if tip is None:
            return {}
        rewritten = None
        if cfg.nudge.auto_rewrite and tip != _TIP_PASTE:  # never rewrite a paste
            llm = _make_llm(cfg)
            if llm is not None:
                rewritten = rewrite_prompt(prompt, llm, context=gather_context(payload))
        _record_audit(cfg, payload, prompt, trigger=tip, refined=rewritten)
        return _auto_rewrite_response(rewritten, cfg.llm.model) if rewritten else _AUTO_RESPONSE

    if mode == "hint":
        # Hint mode: let the prompt through untouched. The Stop hook
        # delivers the tip after the response instead.
        tip = _tip_for(prompt)
        record_prompt(cfg.cache_dir, prompt, was_nudged=False)
        return {}

    if mode == "always":
        if _is_machine_prompt(prompt):
            return {}  # harness traffic, not a prompt anyone typed
        if prompt.strip().lower() in _BARE_CONFIRMATIONS:
            return {}  # answering Claude, not a request -- nothing to rewrite
        if is_bare_paste(prompt):
            # A rewrite is kept "about as long as a well-written ask" (see
            # _REWRITE_SYSTEM), so rewriting a mostly-pasted prompt risks
            # dropping the pasted material -- /prompt-coach:accept would then
            # send the ask without it. Tip instead, never block.
            _record_audit(cfg, payload, prompt, trigger=_TIP_PASTE)
            record_prompt(cfg.cache_dir, prompt, was_nudged=True)
            return {"systemMessage": _TIP_PASTE}
        llm = _make_llm(cfg)
        context = gather_context(payload) if llm is not None else None
        result = _rewrite_or_fallback(
            prompt,
            llm,
            fallback_message=None,
            cache_dir=cfg.cache_dir,
            session_id=session_id,
            context=context,
        )
        if result.get("decision") == "block":
            refined = _peek_pending_rewrite(cfg.cache_dir, session_id)
            _record_audit(cfg, payload, prompt, trigger="always_mode", refined=refined)
        record_prompt(cfg.cache_dir, prompt, was_nudged=result.get("decision") == "block")
        return result

    # "coach": only the calibrated triggers, once per session.
    tip = _tip_for(prompt)
    if tip is None or session_id in _load_nudged_sessions(cfg.cache_dir):
        return {}
    _record_nudge(cfg.cache_dir, session_id)
    if tip == _TIP_PASTE:
        # Same reason as always-mode above: never rewrite-and-block a paste.
        _record_audit(cfg, payload, prompt, trigger=tip)
        record_prompt(cfg.cache_dir, prompt, was_nudged=True)
        return {"systemMessage": tip}
    llm = _make_llm(cfg)
    context = gather_context(payload) if llm is not None else None
    result = _rewrite_or_fallback(
        prompt,
        llm,
        fallback_message=tip,
        cache_dir=cfg.cache_dir,
        session_id=session_id,
        context=context,
    )
    if result.get("decision") == "block":
        refined = _peek_pending_rewrite(cfg.cache_dir, session_id)
        _record_audit(cfg, payload, prompt, trigger=tip, refined=refined)
    else:
        _record_audit(cfg, payload, prompt, trigger=tip)
    record_prompt(cfg.cache_dir, prompt, was_nudged=result.get("decision") == "block")
    return result


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


def _hint_for(prompt: str) -> str | None:
    """Same triggers as _tip_for but never filtered by once-per-session gate.
    Used by hint mode and auto mode (which also has no gating)."""
    return _tip_for(prompt)


def hook_response_stop(
    session_id: str,
    transcript_path: Path,
    cache_dir: Path,
    cfg: Config | None = None,
    payload: dict | None = None,
    hint_mode: bool = False,
) -> dict:
    """Build the Stop hook JSON response: always the plain non-blocking
    `systemMessage` tip (Stop can't block-and-offer-a-rewrite the way
    UserPromptSubmit can -- see module docstring), reading the prompt from
    the transcript since Stop's stdin carries no prompt text."""
    prompt = _last_human_prompt(transcript_path)
    if prompt is None:
        return {}
    if hint_mode:
        tip = _hint_for(prompt)
    else:
        tip = evaluate(prompt, session_id, cache_dir)
    if tip and cfg:
        _record_audit(
            cfg,
            payload or {"session_id": session_id, "hook_event_name": "Stop"},
            prompt,
            trigger=tip,
        )
    record_prompt(cache_dir, prompt, was_nudged=tip is not None)
    return {"systemMessage": tip} if tip else {}
