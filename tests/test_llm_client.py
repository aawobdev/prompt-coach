"""LocalLLM tests: privacy guard, JSON handling, re-prompt, unavailability."""

import httpx
import pytest
import respx

from prompt_coach.llm import prompts as tpl
from prompt_coach.llm.client import (
    LLMUnavailable,
    LocalLLM,
    RemoteEndpointRefused,
    _extract_json,
)

BASE = "http://192.168.1.123:11434/v1"


def completion(payload: str) -> dict:
    return {
        "id": "x",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": payload},
                "finish_reason": "stop",
            }
        ],
    }


class TestGuard:
    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8080/v1",
            "http://127.0.0.1:11434/v1",
            "http://192.168.1.123:11434/v1",
            "http://10.0.0.5:11434/v1",
            "http://al.desk.local:11434/v1",
        ],
    )
    def test_private_urls_accepted(self, url):
        LocalLLM(url, model="m")

    @pytest.mark.parametrize(
        "url",
        ["https://api.openai.com/v1", "https://openrouter.ai/api/v1", "http://8.8.8.8/v1"],
    )
    def test_public_urls_refused(self, url):
        with pytest.raises(RemoteEndpointRefused):
            LocalLLM(url, model="m")

    def test_allow_remote_overrides(self):
        LocalLLM("https://api.openai.com/v1", model="m", allow_remote=True)


class TestCompleteJson:
    @respx.mock
    def test_happy_path(self):
        respx.post(f"{BASE}/chat/completions").respond(json=completion('{"a": 1}'))
        llm = LocalLLM(BASE, model="m")
        assert llm.complete_json("sys", "user") == {"a": 1}

    @respx.mock
    def test_malformed_then_valid_reprompts_once(self):
        route = respx.post(f"{BASE}/chat/completions")
        route.side_effect = [
            httpx.Response(200, json=completion("sure! here you go:")),
            httpx.Response(200, json=completion('```json\n{"fixed": true}\n```')),
        ]
        llm = LocalLLM(BASE, model="m")
        assert llm.complete_json("sys", "user") == {"fixed": True}
        assert route.call_count == 2

    @respx.mock
    def test_malformed_twice_raises(self):
        respx.post(f"{BASE}/chat/completions").respond(json=completion("not json at all"))
        llm = LocalLLM(BASE, model="m")
        with pytest.raises(LLMUnavailable):
            llm.complete_json("sys", "user")

    @respx.mock
    def test_connect_error_raises_unavailable(self):
        respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ConnectError("down"))
        llm = LocalLLM(BASE, model="m")
        with pytest.raises(LLMUnavailable):
            llm.complete_json("sys", "user")

    @respx.mock
    def test_available_probe(self):
        respx.get(f"{BASE}/models").respond(json={"data": []})
        assert LocalLLM(BASE, model="m").available()

    @respx.mock
    def test_available_probe_down(self):
        respx.get(f"{BASE}/models").mock(side_effect=httpx.ConnectError("down"))
        assert not LocalLLM(BASE, model="m").available()


class TestExtractJson:
    def test_plain(self):
        assert _extract_json('{"x": 1}') == {"x": 1}

    def test_fenced(self):
        assert _extract_json('Here:\n```json\n{"x": 1}\n```\ndone') == {"x": 1}

    def test_embedded_in_prose(self):
        assert _extract_json('The result is {"x": 1} as requested.') == {"x": 1}

    @pytest.mark.parametrize("bad", ["", "no braces here", "[1,2]", "{broken"])
    def test_bad_raises(self, bad):
        with pytest.raises(ValueError):
            _extract_json(bad)


class TestTemplates:
    def test_all_templates_format(self):
        block = tpl.format_prompts_block(["prompt one", "x" * 3000])
        assert '1. """prompt one"""' in block
        assert "..." in block  # truncation applied
        tpl.RUBRIC_JUDGE.format(prompts_block=block)
        tpl.PATTERN_MAP.format(prompts_block=block)
        tpl.PATTERN_REDUCE.format(digests_block="{}")
        out = tpl.QUERY_ANSWER.format(
            question="what did I do?",
            excerpts_block=tpl.format_excerpts_block([("hermes:s1:1", "content here")]),
        )
        assert "[1] (hermes:s1:1)" in out

    def test_versions_exist(self):
        assert tpl.RUBRIC_JUDGE_VERSION.startswith("rubric-judge.")
        assert tpl.PATTERN_MAP_VERSION.startswith("pattern-map.")
        assert tpl.PATTERN_REDUCE_VERSION.startswith("pattern-reduce.")
        assert tpl.QUERY_ANSWER_VERSION.startswith("query-answer.")
