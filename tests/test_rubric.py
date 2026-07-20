"""Rubric tests: applicability, deterministic scoring, LLM merge, aggregation."""

from datetime import UTC, datetime, timedelta

import pytest

from prompt_coach.analysis.rubric import (
    LLM_RULES,
    RULE_TITLES,
    aggregate,
    run_rubric,
    score_prompt_deterministic,
    score_sample_llm,
    stratified_sample,
)
from prompt_coach.cache import CacheDB
from prompt_coach.llm.client import LLMUnavailable
from prompt_coach.models import Prompt, PromptOrigin, SourceKind
from prompt_coach.stores.base import content_hash

STRONG = (
    "Write a Python function `slugify(title: str) -> str` in src/utils/text.py.\n"
    "Requirements:\n- lowercase\n- spaces become hyphens\n- max 80 chars\n\n"
    "Output only the function with a docstring, no prose. Example:\n"
    "slugify('Hello World') == 'hello-world'\n"
    "Constraint: stdlib only. Return exactly one file."
)
WEAK = "make the dashboard better"

MACHINE_GOOD = (
    "TASK: create Customer CRUD at src/app/api/route.ts\n"
    "Reasoning: no_think, mechanical work.\n"
    "Sampling: temperature 0.\n"
    "Output: one file, route.ts with GET and POST handlers.\n"
    "After writing, re-read the file and confirm handlers compile.\n"
    "Verify: curl localhost:3000/api returns JSON."
)


def make(content, origin=PromptOrigin.HUMAN, session="s1", ref="0", ts_offset=0):
    return Prompt(
        source=SourceKind.JSON_IMPORT,
        session_id=session,
        message_ref=ref,
        content=content,
        content_hash=content_hash(content),
        timestamp=datetime(2026, 7, 1, tzinfo=UTC) + timedelta(seconds=ts_offset),
        origin=origin,
    )


class StubLLM:
    """Returns a fixed per-prompt score payload; counts calls."""

    model = "stub"

    def __init__(self, scores=None, fail=False):
        self.calls = 0
        self.fail = fail
        self.scores = scores or {"A1": 0.5, "A2": 0.5, "A3": 0.5, "A8": 0.5}

    def complete_json(self, system, user, **kw):
        self.calls += 1
        if self.fail:
            raise LLMUnavailable("down")
        return {str(i): dict(self.scores) for i in range(1, 10)}


@pytest.fixture
def cache(tmp_path):
    db = CacheDB(tmp_path / "cache.db")
    yield db
    db.close()


def rules_of(scores):
    return {s.rule: s for s in scores}


class TestDeterministic:
    def test_strong_beats_weak_on_structure_and_contract(self):
        strong = rules_of(score_prompt_deterministic(make(STRONG)))
        weak = rules_of(score_prompt_deterministic(make(WEAK)))
        assert strong["A4"].score == 1.0
        assert strong["A5"].score == 1.0
        assert strong["A6"].score == 1.0
        assert weak["A4"].score is None  # too short to require structure
        assert weak["A5"].score == 0.0
        assert weak["A6"].score == 0.0

    def test_orchestration_rules_na_for_human(self):
        scores = rules_of(score_prompt_deterministic(make(STRONG)))
        for rule in ("A7", "A9", "A10", "A13"):
            assert scores[rule].score is None

    def test_orchestration_rules_scored_for_machine(self):
        scores = rules_of(
            score_prompt_deterministic(make(MACHINE_GOOD, origin=PromptOrigin.MACHINE))
        )
        assert scores["A7"].score == 1.0
        assert scores["A9"].score == 1.0
        assert scores["A10"].score == 1.0
        assert scores["A13"].score == 1.0

    def test_a12_never_scored(self):
        for prompt in (make(STRONG), make(MACHINE_GOOD, origin=PromptOrigin.MACHINE)):
            assert rules_of(score_prompt_deterministic(prompt))["A12"].score is None


class TestSampling:
    def test_deterministic_with_seed(self):
        prompts = [make(f"prompt number {i}", ref=str(i), ts_offset=i) for i in range(100)]
        a = stratified_sample(prompts, 20, seed=42)
        b = stratified_sample(prompts, 20, seed=42)
        assert [p.message_ref for p in a] == [p.message_ref for p in b]
        assert len(a) == 20

    def test_small_corpus_returned_whole(self):
        prompts = [make(f"p{i}", ref=str(i)) for i in range(5)]
        assert len(stratified_sample(prompts, 100)) == 5


class TestLLMJudge:
    def test_scores_merged_and_cached(self, cache):
        prompts = [make(f"prompt {i}", ref=str(i), ts_offset=i) for i in range(10)]
        llm = StubLLM()
        scores = score_sample_llm(prompts, llm, cache, sample_size=10, batch_size=5)
        assert {s.rule for s in scores} == set(LLM_RULES)
        first_calls = llm.calls
        assert first_calls == 2  # two batches
        # Second run: served entirely from cache.
        score_sample_llm(prompts, llm, cache, sample_size=10, batch_size=5)
        assert llm.calls == first_calls


class TestAggregate:
    def test_by_origin_and_coverage(self):
        scores = (
            score_prompt_deterministic(make(STRONG))
            + score_prompt_deterministic(make(WEAK, session="s2"))
            + score_prompt_deterministic(
                make(MACHINE_GOOD, origin=PromptOrigin.MACHINE, session="s3")
            )
        )
        summary = aggregate(scores)
        by_rule = {r.rule: r for r in summary.rules}
        assert set(by_rule) == set(RULE_TITLES)
        assert by_rule["A5"].human_mean == 0.5  # strong 1.0, weak 0.0
        assert by_rule["A7"].human_mean is None
        assert by_rule["A7"].machine_mean == 1.0
        assert not by_rule["A12"].applicable
        assert by_rule["A5"].coverage == 3
        assert by_rule["A5"].best_ref is not None


class TestRunRubric:
    def test_llm_failure_degrades_to_deterministic(self, cache):
        prompts = [make(STRONG), make(WEAK, session="s2")]
        summary = run_rubric(prompts, StubLLM(fail=True), cache)
        assert summary.sampled_llm == 0
        by_rule = {r.rule: r for r in summary.rules}
        assert by_rule["A5"].applicable  # deterministic rules still present
        assert not by_rule["A1"].applicable  # llm rules absent

    def test_no_llm_at_all(self, cache):
        summary = run_rubric([make(STRONG)], None, cache)
        assert summary.sampled_llm == 0

    def test_micro_replies_excluded_from_scoring(self, cache):
        micro = [make(c, session=f"m{i}", ref=str(i)) for i, c in enumerate(["1", "y", "gg"])]
        summary = run_rubric([make(STRONG), *micro], None, cache)
        by_rule = {r.rule: r for r in summary.rules}
        assert by_rule["A5"].coverage == 1  # only the substantive prompt scored
