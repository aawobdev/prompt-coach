"""JSON import store tests, driven by the shared sample_sessions.json fixture."""

from datetime import UTC, datetime

from prompt_coach.models import PromptOrigin, SourceKind
from prompt_coach.stores.json_import import JsonImportStore


def test_discover_counts(sample_sessions_path, sample_sessions):
    info = JsonImportStore(sample_sessions_path).discover()
    assert info.available
    assert info.session_count == len(sample_sessions)
    expected_prompts = sum(
        1 for s in sample_sessions for m in s["messages"] if m["role"] == "user" and m["content"]
    )
    assert info.prompt_count == expected_prompts


def test_iter_prompts_user_only(sample_sessions_path):
    prompts = list(JsonImportStore(sample_sessions_path).iter_prompts())
    assert all(p.source is SourceKind.JSON_IMPORT for p in prompts)
    assert not any("Could you clarify" in p.content for p in prompts)


def test_machine_classification(sample_sessions_path):
    prompts = list(JsonImportStore(sample_sessions_path).iter_prompts())
    by_session = {}
    for p in prompts:
        by_session.setdefault(p.session_id, []).append(p)
    assert all(p.origin is PromptOrigin.MACHINE for p in by_session["machine-1"])
    assert all(p.origin is PromptOrigin.HUMAN for p in by_session["explicit-1"])


def test_refinement_chain_ordering(sample_sessions_path):
    prompts = [
        p
        for p in JsonImportStore(sample_sessions_path).iter_prompts()
        if p.session_id == "iterate-1"
    ]
    assert len(prompts) == 3
    assert [p.message_ref for p in prompts] == ["iterate-1:0", "iterate-1:1", "iterate-1:2"]


def test_since_filter(sample_sessions_path):
    since = datetime(2026, 7, 5, 0, 0, tzinfo=UTC)
    prompts = list(JsonImportStore(sample_sessions_path).iter_prompts(since=since))
    assert {p.session_id for p in prompts} == {"machine-1", "structured-1", "context-1"}


def test_missing_file_unavailable(tmp_path):
    store = JsonImportStore(tmp_path / "nope.json")
    assert not store.discover().available
