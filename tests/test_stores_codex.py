"""Codex CLI store tests: event-log parsing, IDE-wrapper stripping, offset resume."""

import json

from prompt_coach.models import PromptOrigin
from prompt_coach.stores.codex_cli import CodexStore


def session_meta(session_id="019a0c1e-c987-7fe3-a8c3-14f0c80c84ee", cwd="c:\\proj"):
    return json.dumps(
        {
            "timestamp": "2026-07-01T12:00:00.000Z",
            "type": "session_meta",
            "payload": {"id": session_id, "cwd": cwd, "originator": "codex_vscode"},
        }
    )


def user_message(text, kind="plain", ts="2026-07-01T12:00:05.000Z"):
    return json.dumps(
        {
            "timestamp": ts,
            "type": "event_msg",
            "payload": {"type": "user_message", "message": text, "kind": kind},
        }
    )


def other_event(payload_type="agent_reasoning"):
    return json.dumps(
        {
            "timestamp": "2026-07-01T12:00:06.000Z",
            "type": "response_item",
            "payload": {"type": payload_type},
        }
    )


def write_session(tmp_path, name, lines):
    day = tmp_path / "2026" / "07" / "01"
    day.mkdir(parents=True, exist_ok=True)
    f = day / f"rollout-2026-07-01T12-00-00-{name}.jsonl"
    f.write_text("\n".join(lines) + "\n")
    return f


def test_plain_message_extracted_with_cwd(tmp_path):
    write_session(
        tmp_path,
        "s1",
        [session_meta(cwd="c:\\proj\\wedding"), user_message("fix the layout bug")],
    )
    prompts = list(CodexStore(tmp_path).iter_prompts())
    assert len(prompts) == 1
    assert prompts[0].content == "fix the layout bug"
    assert prompts[0].cwd == "c:\\proj\\wedding"
    assert prompts[0].git_repo == "c:\\proj\\wedding"
    assert prompts[0].origin is PromptOrigin.HUMAN
    assert prompts[0].session_id.endswith("s1")


def test_ide_wrapper_stripped_to_actual_request(tmp_path):
    wrapped = (
        "# Context from my IDE setup:\n\n## Active file: main.scss\n\n"
        "## My request for Codex:\nmake the header sticky\n"
    )
    write_session(tmp_path, "s2", [session_meta(), user_message(wrapped)])
    prompts = list(CodexStore(tmp_path).iter_prompts())
    assert prompts[0].content == "make the header sticky"


def test_environment_context_dropped(tmp_path):
    write_session(
        tmp_path,
        "s3",
        [
            session_meta(),
            user_message(
                "<environment_context>...</environment_context>", kind="environment_context"
            ),
        ],
    )
    assert list(CodexStore(tmp_path).iter_prompts()) == []


def test_non_user_message_events_ignored(tmp_path):
    write_session(
        tmp_path,
        "s4",
        [session_meta(), other_event("agent_reasoning"), other_event("function_call"), "{not json"],
    )
    assert list(CodexStore(tmp_path).iter_prompts()) == []


def test_empty_message_skipped(tmp_path):
    write_session(tmp_path, "s5", [session_meta(), user_message("   ")])
    assert list(CodexStore(tmp_path).iter_prompts()) == []


def test_offset_resume(tmp_path):
    f = write_session(tmp_path, "s6", [session_meta(), user_message("before resume")])
    store = CodexStore(tmp_path)
    results = list(store.iter_file(f))
    assert [p.content for _, p in results] == ["before resume"]
    last = results[-1][0]
    with open(f, "a") as fh:
        fh.write(user_message("after resume") + "\n")
    resumed = list(store.iter_file(f, from_offset=last))
    assert [p.content for _, p in resumed] == ["after resume"]
    assert resumed[0][1].session_id == results[0][1].session_id
    # session_meta (and its cwd) is upstream of the resume point.
    assert resumed[0][1].cwd is None


def test_discover(tmp_path):
    write_session(tmp_path, "s7", [session_meta(), user_message("hello codex")])
    info = CodexStore(tmp_path).discover()
    assert info.available
    assert info.session_count == 1


def test_missing_dir_unavailable(tmp_path):
    info = CodexStore(tmp_path / "nope").discover()
    assert not info.available
    assert info.detail == "not found"
