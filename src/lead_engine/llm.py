"""LLM client abstraction with retries. The pipeline depends on the Protocol, not
on the OpenAI SDK directly, so tests can inject a fake client with no network.
"""

from __future__ import annotations

import json
import os
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(Exception):
    """Non-recoverable, or retries-exhausted, LLM failure."""


class LLMRefusal(LLMError):
    """The model declined to answer. Never retried."""


@dataclass
class LLMResult:
    data: dict[str, Any]
    latency_ms: float
    input_tokens: int | None
    output_tokens: int | None
    model: str


class LLMClient(Protocol):
    def extract(self, *, system_prompt: str, user_content: str, schema: dict[str, Any]) -> LLMResult: ...


_RETRYABLE_NAMES = {"APITimeoutError", "APIConnectionError", "RateLimitError", "InternalServerError"}


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    if type(exc).__name__ in _RETRYABLE_NAMES:
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and (status in (408, 409, 429) or status >= 500)


class OpenAIClient:
    """Chat Completions with Structured Outputs (strict JSON schema)."""

    def __init__(
        self,
        model: str = "gpt-4o",
        *,
        api_key: str | None = None,
        timeout_s: float = 30.0,
        max_retries: int = 3,
        base_delay_s: float = 1.0,
        client: Any = None,
        sleep: Callable[[float], None] = time.sleep,
        jitter: bool = True,
    ) -> None:
        if client is None:
            from openai import OpenAI  # imported lazily so tests need no SDK/network

            key = api_key or os.environ.get("OPENAI_API_KEY")
            if not key:
                raise LLMError("OPENAI_API_KEY is not set")
            client = OpenAI(api_key=key, max_retries=0)  # we own the retry policy
        self._client = client
        self.model = model
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.base_delay_s = base_delay_s
        self._sleep = sleep
        self._jitter = jitter

    def extract(self, *, system_prompt: str, user_content: str, schema: dict[str, Any]) -> LLMResult:
        attempt = 0
        while True:
            t0 = time.perf_counter()
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    temperature=0,
                    timeout=self.timeout_s,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    response_format={"type": "json_schema", "json_schema": schema},
                )
            except Exception as exc:  # noqa: BLE001 - classified via is_retryable below
                if is_retryable(exc) and attempt < self.max_retries:
                    delay = self.base_delay_s * (2**attempt)
                    if self._jitter:
                        delay += random.uniform(0, delay * 0.25)  # noqa: S311 - not security-sensitive
                    self._sleep(delay)
                    attempt += 1
                    continue
                raise LLMError(f"{type(exc).__name__}: {exc}") from exc
            latency_ms = (time.perf_counter() - t0) * 1000
            return self._parse(resp, latency_ms)

    def _parse(self, resp: Any, latency_ms: float) -> LLMResult:
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMRefusal(str(choice.message.refusal))
        if choice.finish_reason == "length":
            raise LLMError("output truncated (finish_reason=length)")
        try:
            data = json.loads(choice.message.content)
        except (TypeError, json.JSONDecodeError) as exc:
            raise LLMError(f"invalid JSON from model: {exc}") from exc
        usage = getattr(resp, "usage", None)
        return LLMResult(
            data=data,
            latency_ms=latency_ms,
            input_tokens=getattr(usage, "prompt_tokens", None),
            output_tokens=getattr(usage, "completion_tokens", None),
            model=self.model,
        )
