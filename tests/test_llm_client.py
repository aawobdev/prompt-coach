"""LocalLLM tests: privacy guard, JSON handling, re-prompt, unavailability."""

import json

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

BASE = "http://192.168.1.10:11434/v1"


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
            "http://192.168.1.10:11434/v1",
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


class TestHttp:
    """The plain-httpx request path (the openai SDK was dropped 2026-10-10)."""

    @pytest.fixture(autouse=True)
    def _no_retry_sleep(self, monkeypatch):
        monkeypatch.setattr("prompt_coach.llm.client._RETRY_DELAY", 0)

    @respx.mock
    @pytest.mark.parametrize("status", [400, 401, 404, 422])
    def test_client_error_raises_without_retry(self, status):
        route = respx.post(f"{BASE}/chat/completions").respond(status, text="echo: secret prompt")
        with pytest.raises(LLMUnavailable) as exc:
            LocalLLM(BASE, "m").complete_json("s", "u")
        assert f"HTTP {status}" in str(exc.value)
        assert "secret prompt" not in str(exc.value)  # body may echo the prompt
        assert route.call_count == 1

    @respx.mock
    def test_server_error_retried_once_then_succeeds(self):
        route = respx.post(f"{BASE}/chat/completions")
        route.side_effect = [
            httpx.Response(503),
            httpx.Response(200, json=completion('{"a": 1}')),
        ]
        assert LocalLLM(BASE, "m").complete_json("s", "u") == {"a": 1}
        assert route.call_count == 2

    @respx.mock
    def test_server_error_twice_raises(self):
        route = respx.post(f"{BASE}/chat/completions").respond(500)
        with pytest.raises(LLMUnavailable, match="HTTP 500"):
            LocalLLM(BASE, "m").complete_json("s", "u")
        assert route.call_count == 2

    @respx.mock
    def test_connect_error_retried_once(self):
        route = respx.post(f"{BASE}/chat/completions")
        route.side_effect = [
            httpx.ConnectError("blip"),
            httpx.Response(200, json=completion('{"a": 1}')),
        ]
        assert LocalLLM(BASE, "m").complete_json("s", "u") == {"a": 1}

    @respx.mock
    def test_timeout_not_retried(self):
        route = respx.post(f"{BASE}/chat/completions").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(LLMUnavailable, match="ReadTimeout"):
            LocalLLM(BASE, "m").complete_json("s", "u")
        assert route.call_count == 1

    @respx.mock
    @pytest.mark.parametrize(
        "body",
        [{"choices": []}, {"no": "choices"}, {"choices": [{"message": None}]}, ["x"]],
    )
    def test_malformed_body_raises(self, body):
        respx.post(f"{BASE}/chat/completions").respond(json=body)
        with pytest.raises(LLMUnavailable, match="malformed"):
            LocalLLM(BASE, "m").complete_json("s", "u")

    @respx.mock
    def test_non_json_body_raises(self):
        respx.post(f"{BASE}/chat/completions").respond(200, text="<html>proxy page</html>")
        with pytest.raises(LLMUnavailable, match="malformed"):
            LocalLLM(BASE, "m").complete_json("s", "u")

    @respx.mock
    def test_null_content_counts_as_unparseable(self):
        payload = completion("x")
        payload["choices"][0]["message"]["content"] = None
        respx.post(f"{BASE}/chat/completions").respond(json=payload)
        with pytest.raises(LLMUnavailable, match="unparseable"):
            LocalLLM(BASE, "m").complete_json("s", "u")

    @respx.mock
    def test_bearer_header_only_with_a_key(self):
        route = respx.post(f"{BASE}/chat/completions").respond(json=completion('{"a": 1}'))
        LocalLLM(BASE, "m").complete_json("s", "u")
        assert "authorization" not in route.calls.last.request.headers
        LocalLLM(BASE, "m", api_key="k-123").complete_json("s", "u")
        assert route.calls.last.request.headers["authorization"] == "Bearer k-123"

    @respx.mock
    def test_request_shape(self):
        route = respx.post(f"{BASE}/chat/completions").respond(json=completion('{"a": 1}'))
        LocalLLM(BASE + "/", "my-model").complete_json("sys", "usr", max_tokens=42)
        sent = json.loads(route.calls.last.request.content)
        assert sent["model"] == "my-model"
        assert sent["max_tokens"] == 42
        assert sent["temperature"] == 0.0
        assert sent["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "usr"},
        ]

    @respx.mock
    def test_extra_body_cannot_replace_core_fields(self):
        route = respx.post(f"{BASE}/chat/completions").respond(json=completion('{"a": 1}'))
        LocalLLM(BASE, "m", extra_body={"model": "other", "top_k": 5}).complete_json("s", "u")
        sent = json.loads(route.calls.last.request.content)
        assert (sent["model"], sent["top_k"]) == ("m", 5)


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


class TestExtraBody:
    """Provider-specific request fields (e.g. OpenRouter's reasoning switch:
    DeepSeek v4 flash spent its whole 800-token budget reasoning and returned
    no content, measured 2026-09-27) go through extra_body -- and nothing
    extra is sent when it is unset."""

    def _sent_body(self, route):
        return json.loads(route.calls.last.request.content)

    @respx.mock
    def test_extra_body_is_merged_into_request(self):
        route = respx.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(200, json=completion('{"a": 1}'))
        )
        LocalLLM(BASE, "m", extra_body={"reasoning": {"enabled": False}}).complete_json("s", "u")
        assert self._sent_body(route)["reasoning"] == {"enabled": False}

    @respx.mock
    def test_extra_body_unset_sends_nothing_extra(self):
        route = respx.post(f"{BASE}/chat/completions").mock(
            return_value=httpx.Response(200, json=completion('{"a": 1}'))
        )
        LocalLLM(BASE, "m").complete_json("s", "u")
        assert "reasoning" not in self._sent_body(route)
