"""Copilot Chat store tests: event-log parsing, offset resume, discovery."""

import json

from prompt_coach.models import PromptOrigin
from prompt_coach.stores.copilot import CopilotStore


def kind0(requests=()):
    return json.dumps(
        {
            "kind": 0,
            "v": {
                "version": 3,
                "sessionId": "sess-abc",
                "requests": list(requests),
                "inputState": {},
            },
        }
    )


def request(rid, text, ts=1779045221231):
    return {
        "requestId": rid,
        "timestamp": ts,
        "message": {"text": text, "parts": [{"text": text, "kind": "text"}]},
        "agent": {"extensionId": {"value": "GitHub.copilot-chat"}},
    }


def kind2_append(*reqs):
    return json.dumps({"kind": 2, "k": ["requests"], "v": list(reqs)})


def kind1_set(path, value):
    return json.dumps({"kind": 1, "k": path, "v": value})


def write_session(tmp_path, name, lines):
    ws = tmp_path / "ws1" / "chatSessions"
    ws.mkdir(parents=True, exist_ok=True)
    f = ws / f"{name}.jsonl"
    f.write_text("\n".join(lines) + "\n")
    return f


def test_prompts_from_kind0_and_kind2(tmp_path):
    write_session(
        tmp_path,
        "s-1",
        [
            kind0([request("r0", "initial question from state")]),
            kind2_append(request("r1", "first appended prompt")),
            kind1_set(["requests", 0, "result"], {"timings": {}}),
            kind2_append(request("r2", "second appended prompt", ts=1779045254359)),
        ],
    )
    store = CopilotStore(tmp_path)
    prompts = list(store.iter_prompts())
    assert [p.message_ref for p in prompts] == ["r0", "r1", "r2"]
    assert prompts[0].content == "initial question from state"
    assert all(p.session_id == "s-1" for p in prompts)
    assert all(p.origin is PromptOrigin.HUMAN for p in prompts)
    assert prompts[2].timestamp.year == 2026


def test_kind1_and_other_paths_ignored(tmp_path):
    write_session(
        tmp_path,
        "s-2",
        [
            kind0(),
            kind1_set(["inputState", "selectedModel"], {"identifier": "x"}),
            json.dumps({"kind": 2, "k": ["pendingRequests"], "v": [request("rX", "not a prompt")]}),
            json.dumps({"kind": 3, "v": "unknown future event"}),
            "{not json",
        ],
    )
    assert list(CopilotStore(tmp_path).iter_prompts()) == []


def test_empty_message_skipped(tmp_path):
    write_session(tmp_path, "s-3", [kind0(), kind2_append(request("r1", "   "))])
    assert list(CopilotStore(tmp_path).iter_prompts()) == []


def test_offset_resume(tmp_path):
    f = write_session(tmp_path, "s-4", [kind0(), kind2_append(request("r1", "before resume"))])
    store = CopilotStore(tmp_path)
    results = list(store.iter_file(f))
    assert [p.message_ref for _, p in results] == ["r1"]
    last = results[-1][0]
    with open(f, "a") as fh:
        fh.write(kind2_append(request("r2", "after resume")) + "\n")
    resumed = list(store.iter_file(f, from_offset=last))
    assert [p.message_ref for _, p in resumed] == ["r2"]
    # Resumed parse still knows the session id without re-reading kind 0.
    assert resumed[0][1].session_id == "s-4"


def test_discover(tmp_path):
    write_session(tmp_path, "s-5", [kind0(), kind2_append(request("r1", "hello copilot"))])
    info = CopilotStore(tmp_path).discover()
    assert info.available
    assert info.session_count == 1


def test_missing_dir_unavailable(tmp_path):
    info = CopilotStore(tmp_path / "nope").discover()
    assert not info.available
    assert info.detail == "not found"
