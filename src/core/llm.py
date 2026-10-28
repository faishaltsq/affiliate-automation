"""src/core/llm.py — provider abstraction with timeout, retries, quotas.

One endpoint, a model fallback chain, and a fake implementation for tests.
No business module imports `requests` directly after this exists.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Callable, Optional, Protocol

from src.core.config import LLMConfig, get_settings, redact_secrets
from src.core.clock import utc_iso


class LLMError(RuntimeError):
    pass


@dataclass
class LLMResponse:
    text: str
    model: str
    latency_ms: int
    attempts: int


class LLMClient(Protocol):
    def complete(
        self,
        messages: list[dict],
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> LLMResponse: ...


class HttpLLMClient:
    """Real client: POSTs to the configured OpenAI-compatible gateway."""

    def __init__(self, config: Optional[LLMConfig] = None, sleeper: Callable[[float], None] = time.sleep):
        self.config = config or get_settings().llm
        self._sleep = sleeper
        self._calls = 0

    @property
    def call_count(self) -> int:
        return self._calls

    def complete(
        self,
        messages: list[dict],
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        import requests

        chain = [self.config.model] + [
            m for m in self.config.fallback_models if m != self.config.model
        ]
        errors: list[str] = []
        for model in chain:
            for attempt in range(max(1, self.config.max_retries)):
                self._calls += 1
                started = time.monotonic()
                try:
                    r = requests.post(
                        self.config.chat_url,
                        json={
                            "model": model,
                            "messages": messages,
                            "temperature": temperature,
                            "max_tokens": max_tokens,
                            "stream": False,
                        },
                        headers={
                            "Authorization": f"Bearer {self.config.api_key}",
                            "Content-Type": "application/json",
                        },
                        timeout=self.config.timeout_seconds,
                    )
                    r.raise_for_status()
                    data = r.json()
                    choices = data.get("choices") or []
                    if not choices:
                        raise LLMError(f"empty choices from {model}")
                    msg = choices[0].get("message") or {}
                    text = (msg.get("content") or msg.get("reasoning_content") or "").strip()
                    if not text:
                        raise LLMError(f"empty content from {model}")
                    return LLMResponse(
                        text=text,
                        model=model,
                        latency_ms=int((time.monotonic() - started) * 1000),
                        attempts=attempt + 1,
                    )
                except Exception as exc:  # noqa: BLE001 - chain must continue
                    errors.append(f"{model}#{attempt}: {redact_secrets(str(exc))}")
                    if attempt + 1 < max(1, self.config.max_retries):
                        self._sleep(min(2.0 * (2 ** attempt), 8.0))
        raise LLMError("all LLM providers failed: " + "; ".join(errors))


class FakeLLMClient:
    """Deterministic client for tests and offline shadow runs.

    `script` maps a substring matcher to a reply, or is a list of replies
    consumed in order. `raise_on` makes specific calls fail so retry/fallback
    behaviour can be asserted without a network.
    """

    def __init__(
        self,
        script: Optional[dict[str, str]] = None,
        replies: Optional[list[str]] = None,
        raise_on: Optional[set[int]] = None,
        default: str = '{"content": "contoh jawaban"}',
    ):
        self.script = script or {}
        self.replies = list(replies or [])
        self.raise_on = raise_on or set()
        self.default = default
        self.calls: list[dict] = []
        self._idx = 0

    def complete(
        self,
        messages: list[dict],
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> LLMResponse:
        self.calls.append({
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "at": utc_iso(),
        })
        n = len(self.calls) - 1
        if n in self.raise_on:
            raise LLMError(f"scripted failure on call {n}")
        blob = json.dumps(messages, ensure_ascii=False)
        for needle, reply in self.script.items():
            if needle in blob:
                return LLMResponse(text=reply, model="fake", latency_ms=1, attempts=1)
        if self._idx < len(self.replies):
            reply = self.replies[self._idx]
            self._idx += 1
            return LLMResponse(text=reply, model="fake", latency_ms=1, attempts=1)
        return LLMResponse(text=self.default, model="fake", latency_ms=1, attempts=1)


_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    global _client
    if _client is None:
        _client = HttpLLMClient()
    return _client


def set_llm_client(client: Optional[LLMClient]) -> None:
    global _client
    _client = client


def complete_json(
    client: LLMClient,
    messages: list[dict],
    temperature: float = 0.4,
    max_tokens: int = 1024,
) -> dict:
    """Complete and parse JSON. Raises LLMError on anything unparseable.

    Deliberately does NOT repair or invent a payload — a malformed model reply
    must fail closed so the quality gate can reject the draft.
    """
    resp = client.complete(messages, temperature=temperature, max_tokens=max_tokens)
    text = resp.text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1] if "\n" in text else text
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"model returned non-JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise LLMError("model returned JSON that is not an object")
    return data
