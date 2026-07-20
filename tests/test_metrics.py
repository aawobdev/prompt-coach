"""Deterministic style-metrics tests with known-answer inputs."""

from datetime import UTC, datetime

from prompt_coach.analysis.metrics import compute_metrics, estimate_tokens
from prompt_coach.models import Prompt, PromptOrigin, SourceKind
from prompt_coach.stores.base import content_hash
from prompt_coach.stores.json_import import JsonImportStore


def make(content: str, session="s1", ref="0", origin=PromptOrigin.HUMAN, second=0):
    return Prompt(
        source=SourceKind.JSON_IMPORT,
        session_id=session,
        message_ref=ref,
        content=content,
        content_hash=content_hash(content),
        timestamp=datetime(2026, 7, 1, 12, 0, second, tzinfo=UTC),
        origin=origin,
    )


def test_empty_corpus():
    m = compute_metrics([])
    assert m.human is None
    assert m.machine is None
    assert m.overall is None


def test_single_prompt_exact_values():
    p = make("a" * 400)  # 100 estimated tokens
    m = compute_metrics([p])
    seg = m.human
    assert seg.prompt_count == 1
    assert seg.session_count == 1
    assert seg.avg_prompt_tokens == 100.0
    assert seg.median_prompt_tokens == 100.0
    assert seg.prompts_per_session == 1.0
    assert seg.refinement_rate == 0.0
    assert seg.avg_first_message_ratio == 1.0
    assert m.machine is None


def test_refinement_and_first_message_ratio():
    prompts = [
        make("x" * 300, session="s1", ref="0", second=0),
        make("y" * 100, session="s1", ref="1", second=10),
        make("z" * 100, session="s2", ref="0", second=20),
    ]
    seg = compute_metrics(prompts).human
    assert seg.session_count == 2
    assert seg.refinement_rate == 0.5  # s1 refined, s2 did not
    # s1 first-message ratio 300/400 = 0.75; s2 = 1.0; mean 0.875
    assert abs(seg.avg_first_message_ratio - 0.875) < 1e-9


def test_segmentation_by_origin():
    prompts = [
        make("human question"),
        make("TASK: machine spec", session="s2", origin=PromptOrigin.MACHINE),
    ]
    m = compute_metrics(prompts)
    assert m.human.prompt_count == 1
    assert m.machine.prompt_count == 1
    assert m.overall.prompt_count == 2


def test_signal_rates_on_fixture(sample_sessions_path):
    prompts = list(JsonImportStore(sample_sessions_path).iter_prompts())
    m = compute_metrics(prompts)
    # explicit-1 carries example + constraints + structured output; vague-1 none.
    assert 0 < m.human.example_rate < 1
    assert 0 < m.human.constraint_rate <= 1
    assert 0 < m.human.structured_output_rate < 1
    assert m.machine.prompt_count == 1


def test_estimate_tokens():
    assert estimate_tokens("abcd" * 25) == 25.0
