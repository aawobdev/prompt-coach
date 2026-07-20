"""Natural-language questions over the cached prompt history.

Retrieval is FTS5 over the unified cache; the local LLM answers strictly from
the retrieved excerpts with citations. With no LLM reachable, the matching
snippets themselves are returned so the command stays useful offline.
"""

from __future__ import annotations

import hashlib

from prompt_coach.analysis.rubric import prompt_ref
from prompt_coach.cache import CacheDB
from prompt_coach.llm import prompts as tpl
from prompt_coach.llm.client import LLMUnavailable, LocalLLM
from prompt_coach.models import Prompt


def _snippet(p: Prompt, width: int = 200) -> str:
    text = " ".join(p.content.split())
    return text[:width] + ("..." if len(text) > width else "")


def _fallback(hits: list[Prompt]) -> str:
    lines = ["LLM unavailable; showing matching prompts instead:", ""]
    for p in hits:
        lines.append(f"- [{p.timestamp.date().isoformat()}] ({prompt_ref(p)}) {_snippet(p)}")
    return "\n".join(lines)


def answer(question: str, cache: CacheDB, llm: LocalLLM | None, k: int = 12) -> str:
    hits = cache.search(question, limit=k)
    if not hits:
        return "No prompts in the cache match that question. Try `prompt-coach cache sync` first."
    if llm is None:
        return _fallback(hits)

    excerpts = [(prompt_ref(p), p.content) for p in hits]
    key_material = question + "".join(p.content_hash for p in hits)
    key_material += tpl.QUERY_ANSWER_VERSION + llm.model
    key = hashlib.sha256(key_material.encode()).hexdigest()
    payload = cache.get_llm(key)
    if payload is None:
        try:
            payload = llm.complete_json(
                tpl.SYSTEM_ANALYST,
                tpl.QUERY_ANSWER.format(
                    question=question,
                    excerpts_block=tpl.format_excerpts_block(excerpts),
                ),
            )
        except LLMUnavailable:
            return _fallback(hits)
        cache.put_llm(key, payload, llm.model, tpl.QUERY_ANSWER_VERSION)

    text = str(payload.get("answer", "")).strip()
    if not text:
        return _fallback(hits)
    used = payload.get("used_refs") or []
    cited = [hits[i - 1] for i in used if isinstance(i, int) and 1 <= i <= len(hits)]
    if cited:
        text += "\n\nSources:"
        for p in cited:
            text += f"\n- [{p.timestamp.date().isoformat()}] {prompt_ref(p)}"
    if payload.get("confident") is False:
        text += "\n\n(Partial answer: the matching excerpts only partly cover the question.)"
    return text
