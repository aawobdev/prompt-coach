"""ChatGPT export store tests against a synthetic export fixture."""

import json
import zipfile

from prompt_coach.models import PromptOrigin
from prompt_coach.stores.chatgpt_export import ChatGPTExportStore, looks_like_chatgpt_export


def node(role, text, ct=1751364000.0, hidden=False, content_type="text"):
    return {
        "message": {
            "author": {"role": role},
            "create_time": ct,
            "content": {"content_type": content_type, "parts": [text]},
            "metadata": {"is_visually_hidden_from_conversation": hidden},
        }
    }


EXPORT = [
    {
        "conversation_id": "c1",
        "title": "Fix my resume",
        "create_time": 1751360000.0,
        "mapping": {
            "n1": node("user", "rewrite my resume summary section", ct=1751364000.0),
            "n2": node("assistant", "Sure, here it is"),
            "n3": node("user", "make it shorter and punchier", ct=1751364100.0),
            "n4": node("system", "system boilerplate"),
            "n5": node("user", "hidden context", hidden=True),
            "n6": node("user", "image prompt", content_type="multimodal_text"),
            "n7": {"message": None},
        },
    },
    {
        "conversation_id": "c2",
        "title": "Machine spec",
        "create_time": 1751460000.0,
        "mapping": {"m1": node("user", "TASK: generate a schema", ct=1751464000.0)},
    },
]


def test_shape_probe():
    assert looks_like_chatgpt_export(EXPORT)
    assert not looks_like_chatgpt_export([{"session_id": "x", "messages": []}])
    assert not looks_like_chatgpt_export([])
    assert not looks_like_chatgpt_export({"mapping": {}})


def test_user_text_messages_only(tmp_path):
    f = tmp_path / "conversations.json"
    f.write_text(json.dumps(EXPORT))
    prompts = list(ChatGPTExportStore(f).iter_prompts())
    assert [p.content for p in prompts] == [
        "rewrite my resume summary section",
        "make it shorter and punchier",
        "TASK: generate a schema",
    ]
    assert prompts[0].session_id == "c1"
    assert prompts[0].timestamp.year == 2025
    assert prompts[2].origin is PromptOrigin.MACHINE


def test_zip_input(tmp_path):
    z = tmp_path / "export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("conversations.json", json.dumps(EXPORT))
        zf.writestr("user.json", "{}")
    info = ChatGPTExportStore(z).discover()
    assert info.available
    assert info.session_count == 2
    assert info.prompt_count == 3


def test_zip_without_conversations(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("user.json", "{}")
    assert not ChatGPTExportStore(z).discover().available


def test_missing_and_malformed(tmp_path):
    assert not ChatGPTExportStore(tmp_path / "nope.json").discover().available
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert not ChatGPTExportStore(bad).discover().available
