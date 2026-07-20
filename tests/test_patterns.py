"""Pattern map-reduce tests: call counts, caching, output shaping."""

import math
from datetime import UTC, datetime, timedelta

import pytest

from prompt_coach.analysis.patterns import detect_patterns
from prompt_coach.cache import CacheDB
from prompt_coach.models import Prompt, PromptOrigin, SourceKind
from prompt_coach.stores.base import content_hash

MAP_PAYLOAD = {
    "topics": [{"topic": "coding/python", "share": 0.6}, {"topic": "homelab", "share": 0.4}],
    "habits": ["short refinement chains"],
    "weaknesses": [{"pattern": "bundles unrelated asks", "example_prompt": 2}],
    "strengths": [{"pattern": "states output format", "example_prompt": 1}],
}
REDUCE_PAYLOAD = {
    "strengths": ["states output format"],
    "growth_areas": ["bundles unrelated asks", "rarely gives the why"],
    "notable_patterns": ["iterates in short chains"],
    "topics": [{"topic": "coding/python", "share": 0.6}, {"topic": "homelab", "share": 0.4}],
}


class StubLLM:
    model = "stub"

    def __init__(self):
        self.map_calls = 0
        self.reduce_calls = 0

    def complete_json(self, system, user, **kw):
        if "<digests>" in user:
            self.reduce_calls += 1
            return REDUCE_PAYLOAD
        self.map_calls += 1
        return MAP_PAYLOAD


def make(i: int) -> Prompt:
    content = f"prompt number {i} about something"
    return Prompt(
        source=SourceKind.CLAUDE_CODE,
        session_id=f"s{i % 7}",
        message_ref=str(i),
        content=content,
        content_hash=content_hash(content),
        timestamp=datetime(2026, 7, 1, tzinfo=UTC) + timedelta(minutes=i),
        origin=PromptOrigin.HUMAN,
    )


@pytest.fixture
def cache(tmp_path):
    db = CacheDB(tmp_path / "cache.db")
    yield db
    db.close()


def test_map_call_count_and_reduce_shape(cache):
    prompts = [make(i) for i in range(60)]
    llm = StubLLM()
    report = detect_patterns(prompts, llm, cache, sample_size=50, batch_size=20)
    assert llm.map_calls == math.ceil(50 / 20)
    assert llm.reduce_calls == 1
    assert report.sample_size == 50
    assert report.strengths == ("states output format",)
    assert report.growth_areas[0] == "bundles unrelated asks"
    assert [t.topic for t in report.topics] == ["coding/python", "homelab"]


def test_second_run_fully_cached(cache):
    prompts = [make(i) for i in range(30)]
    llm = StubLLM()
    detect_patterns(prompts, llm, cache, sample_size=30, batch_size=10)
    calls = (llm.map_calls, llm.reduce_calls)
    detect_patterns(prompts, llm, cache, sample_size=30, batch_size=10)
    assert (llm.map_calls, llm.reduce_calls) == calls


def test_malformed_reduce_fields_shaped_to_empty(cache):
    class OddLLM(StubLLM):
        def complete_json(self, system, user, **kw):
            if "<digests>" in user:
                return {"strengths": "not a list", "topics": {"topic": "x"}}
            return MAP_PAYLOAD

    report = detect_patterns([make(i) for i in range(5)], OddLLM(), cache, sample_size=5)
    assert report.strengths == ()
    assert report.topics == ()


def test_empty_corpus(cache):
    report = detect_patterns([], StubLLM(), cache)
    assert report.sample_size == 0
    assert report.strengths == ()
