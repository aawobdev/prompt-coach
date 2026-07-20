"""Local LLM client. The privacy guarantee is enforced here, not just documented.

The constructor refuses any base URL that does not resolve to a loopback or
private (RFC1918) host unless allow_remote=True is passed explicitly. This
exists because the llm-api gateway can silently fall back to cloud providers:
pointing prompt-coach at a non-local endpoint must be a deliberate act.
"""

from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import urlparse

import httpx
import openai


class LLMUnavailable(RuntimeError):
    """The local model cannot be reached or will not produce valid output."""


class RemoteEndpointRefused(ValueError):
    """Base URL is not local/private and allow_remote was not set."""


def _is_private_host(base_url: str) -> bool:
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
        api_key: str = "ollama",
        timeout: float = 120.0,
        allow_remote: bool = False,
    ):
        if not allow_remote and not _is_private_host(base_url):
            raise RemoteEndpointRefused(
                f"{base_url} is not a local/private endpoint. Prompt content never"
                " leaves the machine unless you set allow_remote = true in config,"
                " and you should only do that for a gateway pinned to local routing."
            )
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self._client = openai.OpenAI(
            base_url=self.base_url, api_key=api_key, timeout=timeout, max_retries=1
        )

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
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            except openai.OpenAIError as exc:
                raise LLMUnavailable(f"local model call failed: {type(exc).__name__}") from exc
            text = resp.choices[0].message.content or ""
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
