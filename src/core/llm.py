"""src/core/llm.py — provider abstraction with timeout, retries, quotas.

One endpoint, a model fallback chain, and a fake implementation for tests.
No business module imports `requests` directly after this exists.
"""
from __future__ import annotations

import json
import re
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


def _strip_code_fence(text: str) -> str:
    """Remove a surrounding markdown fence, however many backticks it uses.

    Models emit ```json, ``` and — observed in production — a bare `json with
    no closing fence at all. A fence that is not stripped reaches json.loads
    and fails at position 0, which is indistinguishable from a genuinely
    malformed payload.
    """
    text = text.strip()
    if not text.startswith("`"):
        return text
    # Drop the opening run of backticks plus an optional language tag.
    text = text.lstrip("`")
    first_newline = text.find("\n")
    if first_newline != -1:
        tag = text[:first_newline].strip().lower()
        if tag in ("", "json", "json5", "javascript", "js"):
            text = text[first_newline + 1:]
    # Drop a closing fence if one is present.
    stripped = text.rstrip()
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    elif stripped.endswith("`"):
        stripped = stripped.rstrip("`")
    return stripped.strip()


def _repair_missing_commas(text: str) -> str:
    """Insert commas between members and elements separated only by newlines.

    A real model reply observed in production emitted one entry per line with
    no separators at all, in both object bodies and arrays. The payload carries
    every field the schema asked for; only the punctuation is absent, so
    recovering it preserves the critic's judgement instead of discarding a
    usable verdict.

    The match requires the previous line to end with a value terminator
    (`}`, `]`, `"` or a digit) and the next to begin with a `"`. A comma that
    is already present is left alone, so valid JSON is returned byte-identical
    — which is what makes it safe to run this unconditionally on a parse
    failure rather than only on a known-bad shape.
    """
    return re.sub(r'(?<=[}\]"\d])\s*\n\s*(?=")', ",\n", text)


def complete_json(
    client: LLMClient,
    messages: list[dict],
    temperature: float = 0.4,
    max_tokens: int = 1024,
) -> dict:
    """Complete and parse JSON. Raises LLMError on anything unparseable.

    Deliberately does NOT invent a payload — a reply missing required fields
    still fails closed so the quality gate can reject the draft. What it does
    recover is presentation damage the model adds around an otherwise complete
    object: markdown fences and omitted separators.
    """
    resp = client.complete(messages, temperature=temperature, max_tokens=max_tokens)
    text = _strip_code_fence(resp.text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as first_error:
        repaired = _repair_missing_commas(text)
        if repaired == text:
            raise LLMError(
                f"model returned non-JSON: {first_error}"
            ) from first_error
        try:
            data = json.loads(repaired)
        except json.JSONDecodeError as exc:
            raise LLMError(
                f"model returned non-JSON: {first_error}"
            ) from exc
    if not isinstance(data, dict):
        raise LLMError("model returned JSON that is not an object")
    return data
