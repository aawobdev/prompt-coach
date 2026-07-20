"""LLM pattern detection: map-reduce over a stratified sample.

map: each batch of prompts becomes a digest (topics, habits, strengths,
weaknesses). reduce: one call merges the digests into the coaching view.
Topic distribution comes out of this pass too; a separate per-session topic
stage would be bulk LLM work for no extra signal (prompting-standards B7).
Every call is memoised in the cache keyed by content + template version +
model, so re-runs only pay for new prompts.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from prompt_coach.analysis.rubric import stratified_sample
from prompt_coach.cache import CacheDB
from prompt_coach.llm import prompts as tpl
from prompt_coach.llm.client import LocalLLM
from prompt_coach.models import PatternReport, Prompt, TopicShare

_MAX_PAYLOAD_CHARS = 32_000  # ~8k estimated tokens; conservative for local num_ctx


def _key(parts: list[str], model: str, version: str) -> str:
    h = hashlib.sha256()
    for part in sorted(parts):
        h.update(part.encode())
    h.update(version.encode())
    h.update(model.encode())
    return h.hexdigest()


def _map_call(batch: Sequence[Prompt], llm: LocalLLM, cache: CacheDB) -> dict:
    key = _key([p.content_hash for p in batch], llm.model, tpl.PATTERN_MAP_VERSION)
    digest = cache.get_llm(key)
    if digest is None:
        block = tpl.format_prompts_block([p.content for p in batch])[:_MAX_PAYLOAD_CHARS]
        digest = llm.complete_json(tpl.SYSTEM_ANALYST, tpl.PATTERN_MAP.format(prompts_block=block))
        cache.put_llm(key, digest, llm.model, tpl.PATTERN_MAP_VERSION)
    return digest


def _reduce_call(digests: list[dict], llm: LocalLLM, cache: CacheDB) -> dict:
    serialised = [json.dumps(d, sort_keys=True) for d in digests]
    key = _key(serialised, llm.model, tpl.PATTERN_REDUCE_VERSION)
    merged = cache.get_llm(key)
    if merged is None:
        block = "\n".join(serialised)[:_MAX_PAYLOAD_CHARS]
        merged = llm.complete_json(
            tpl.SYSTEM_ANALYST, tpl.PATTERN_REDUCE.format(digests_block=block)
        )
        cache.put_llm(key, merged, llm.model, tpl.PATTERN_REDUCE_VERSION)
    return merged


def _str_list(value: object, limit: int = 5) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out = []
    for item in value:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict) and "pattern" in item:
            out.append(str(item["pattern"]))
    return tuple(out[:limit])


def _topics(value: object) -> tuple[TopicShare, ...]:
    if not isinstance(value, list):
        return ()
    out = []
    for item in value:
        if isinstance(item, dict) and "topic" in item:
            try:
                share = float(item.get("share", 0.0))
            except (TypeError, ValueError):
                share = 0.0
            out.append(TopicShare(topic=str(item["topic"]), share=max(0.0, min(1.0, share))))
    out.sort(key=lambda t: t.share, reverse=True)
    return tuple(out[:10])


def detect_patterns(
    prompts: Sequence[Prompt],
    llm: LocalLLM,
    cache: CacheDB,
    *,
    sample_size: int = 300,
    batch_size: int = 25,
    seed: int = 1337,
) -> PatternReport:
    """May raise LLMUnavailable; the caller decides how to degrade."""
    sample = stratified_sample(prompts, sample_size, seed)
    digests = [
        _map_call(sample[i : i + batch_size], llm, cache) for i in range(0, len(sample), batch_size)
    ]
    merged = _reduce_call(digests, llm, cache) if digests else {}
    return PatternReport(
        strengths=_str_list(merged.get("strengths")),
        growth_areas=_str_list(merged.get("growth_areas")),
        notable_patterns=_str_list(merged.get("notable_patterns")),
        topics=_topics(merged.get("topics")),
        sample_size=len(sample),
    )
