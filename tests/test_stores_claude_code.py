"""Claude Code JSONL reader tests: the five acceptance filters and offset resume."""

import json

import pytest

from prompt_coach.models import PromptOrigin
from prompt_coach.stores.claude_code import ClaudeCodeStore, parse_line


def line(**overrides) -> str:
    base = {
        "type": "user",
        "message": {"role": "user", "content": "fix the flaky test in auth"},
        "promptSource": "typed",
        "origin": {"kind": "human"},
        "isSidechain": False,
        "uuid": "u-1",
        "timestamp": "2026-06-22T21:39:40.265Z",
        "sessionId": "sess-1",
        "cwd": "/home/x/proj",
        "gitBranch": "master",
    }
    base.update(overrides)
    return json.dumps(base)


def test_typed_human_prompt_accepted():
    p = parse_line(line())
    assert p is not None
    assert p.content == "fix the flaky test in auth"
    assert p.origin is PromptOrigin.HUMAN
    assert p.session_id == "sess-1"
    assert p.message_ref == "u-1"
    assert p.timestamp.tzinfo is not None


@pytest.mark.parametrize(
    "overrides",
    [
        {"type": "assistant"},
        {"type": "file-history-snapshot"},
        {"origin": {"kind": "hook"}},
        {"origin": None},
        {"promptSource": "resume"},
        {"isSidechain": True},
    ],
)
def test_non_prompt_lines_rejected(overrides):
    assert parse_line(line(**overrides)) is None


def test_command_echo_rejected():
    content = "<command-name>/model</command-name> output"
    assert parse_line(line(message={"role": "user", "content": content})) is None


def test_bare_system_reminder_rejected():
    content = "<system-reminder>some background</system-reminder>"
    assert parse_line(line(message={"role": "user", "content": content})) is None


def test_inline_system_reminder_stripped():
    content = "do the thing <system-reminder>noise</system-reminder> properly"
    p = parse_line(line(message={"role": "user", "content": content}))
    assert p is not None
    assert "system-reminder" not in p.content
    assert "do the thing" in p.content


def test_content_block_array_flattened():
    blocks = [
        {"type": "text", "text": "first part"},
        {"type": "image", "source": {}},
        {"type": "text", "text": "second part"},
    ]
    p = parse_line(line(message={"role": "user", "content": blocks}))
    assert p is not None
    assert p.content == "first part\nsecond part"


def test_malformed_line_skipped():
    assert parse_line("{not json") is None
    assert parse_line("") is None


def test_iter_file_offsets_enable_resume(tmp_path):
    proj = tmp_path / "projects" / "-home-x-proj"
    proj.mkdir(parents=True)
    f = proj / "sess.jsonl"
    f.write_text(line(uuid="u-1") + "\n" + line(uuid="u-2") + "\n")

    store = ClaudeCodeStore(tmp_path / "projects")
    results = list(store.iter_file(f))
    assert [p.message_ref for _, p in results] == ["u-1", "u-2"]

    # Append one more line; resuming from the last offset yields only the new prompt.
    last_offset = results[-1][0]
    with open(f, "a") as fh:
        fh.write(line(uuid="u-3") + "\n")
    resumed = list(store.iter_file(f, from_offset=last_offset))
    assert [p.message_ref for _, p in resumed] == ["u-3"]


def test_discover_and_iter_prompts(tmp_path):
    proj = tmp_path / "projects" / "-home-x-proj"
    proj.mkdir(parents=True)
    (proj / "a.jsonl").write_text(line(uuid="u-1") + "\n" + line(type="assistant") + "\n")
    (proj / "b.jsonl").write_text(line(uuid="u-2", sessionId="sess-2") + "\n")

    store = ClaudeCodeStore(tmp_path / "projects")
    info = store.discover()
    assert info.available
    assert info.session_count == 2
    assert len(list(store.iter_prompts())) == 2


def test_missing_dir_unavailable(tmp_path):
    info = ClaudeCodeStore(tmp_path / "nope").discover()
    assert not info.available
