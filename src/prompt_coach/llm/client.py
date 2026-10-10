"""Client for any OpenAI-compatible endpoint. The privacy guarantee is
enforced here, not just documented.

The constructor refuses any base URL that does not resolve to a loopback or
private (RFC1918) host unless allow_remote=True is passed explicitly. A
gateway can silently fall back to cloud providers, and a cloud endpoint sees
every prompt sent to it: pointing prompt-coach off-machine must be deliberate.

Requests are one plain httpx POST to `{base_url}/chat/completions`, not the
`openai` SDK (DECISIONS.md 2026-10-10): the SDK cost ~680 ms to import inside
the nudge hook, and openai 3 moved to a separate httpx2 stack that respx
can't mock. One request shape doesn't need a client library.
"""

from __future__ import annotations

import ipaddress
import json
import re
import time
from urllib.parse import urlparse

import httpx

# Retried once, like the SDK's max_retries=1: transient overload or a
# dropped connection. Timeouts are not retried -- the attempt already spent
# the caller's whole budget (nudge runs inside a hook with its own timeout).
_RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})
_RETRY_DELAY = 0.5


class LLMUnavailable(RuntimeError):
    """The model cannot be reached or will not produce valid output."""


class RemoteEndpointRefused(ValueError):
    """Base URL is not local/private and allow_remote was not set."""


def is_private_host(base_url: str) -> bool:
    host = urlparse(base_url).hostname or ""
    if host in ("localhost", "host.docker.internal") or host.endswith(".local"):
        return True
    try:
        return ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False  # public DNS name: refuse


class LocalLLM:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        allow_remote: bool = False,
        extra_body: dict | None = None,
    ):
        if not allow_remote and not is_private_host(base_url):
            raise RemoteEndpointRefused(
                f"{base_url} is not a local/private endpoint. Prompt content never"
                " leaves the machine unless you set allow_remote = true in config."
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Sent only when set: see LLMConfig.extra_body.
        self._extra_body = extra_body or None
        # No Authorization header without a key: local servers don't need one.
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(headers=headers, timeout=timeout)

    def available(self) -> bool:
        """Cheap reachability probe; never raises."""
        try:
            httpx.get(f"{self.base_url}/models", timeout=2.0)
            return True
        except httpx.HTTPError:
            return False

    def complete_json(
        self,
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> dict:
        """One completion, parsed as JSON. On a parse failure, re-prompt once
        with the error appended (prompting-standards B5); then give up."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        for attempt in range(2):
            text = self._chat(messages, temperature=temperature, max_tokens=max_tokens)
            try:
                return _extract_json(text)
            except ValueError as exc:
                if attempt == 0:
                    messages.append({"role": "assistant", "content": text})
                    messages.append(
                        {
                            "role": "user",
                            "content": (
                                f"Your reply was not valid JSON ({exc}). Respond again"
                                " with ONLY the JSON object, no prose, no code fences."
                            ),
                        }
                    )
        raise LLMUnavailable("model returned unparseable JSON twice")

    def _chat(self, messages: list[dict], *, temperature: float, max_tokens: int) -> str:
        """One chat completion; returns the reply text. Any transport error,
        non-2xx status or malformed body becomes LLMUnavailable. Error
        messages carry the status or exception type only, never the body:
        it may echo the prompt back."""
        # extra_body first so it can add provider fields but never silently
        # replace the model, messages or token budget.
        body = {
            **(self._extra_body or {}),
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        url = f"{self.base_url}/chat/completions"
        for attempt in range(2):
            last = attempt == 1
            try:
                resp = self._client.post(url, json=body)
            except httpx.TimeoutException as exc:
                raise LLMUnavailable(f"model call failed: {type(exc).__name__}") from exc
            except httpx.HTTPError as exc:
                if last:
                    raise LLMUnavailable(f"model call failed: {type(exc).__name__}") from exc
                time.sleep(_RETRY_DELAY)
                continue
            if resp.status_code in _RETRY_STATUS and not last:
                time.sleep(_RETRY_DELAY)
                continue
            if not resp.is_success:
                raise LLMUnavailable(f"model call failed: HTTP {resp.status_code}")
            try:
                content = resp.json()["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise LLMUnavailable("model returned a malformed response") from exc
            return content if isinstance(content, str) else ""
        raise AssertionError("unreachable")  # pragma: no cover


def _extract_json(text: str) -> dict:
    """Parse a JSON object from model output, tolerating code fences and prose."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    else:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("no JSON object found")
        text = text[start : end + 1]
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc.msg}") from exc
    if not isinstance(obj, dict):
        raise ValueError("top-level JSON value is not an object")
    return obj
