"""Anthropic Claude via the official SDK with structured outputs (output_config.format json_schema)."""

from __future__ import annotations

import time
from typing import Any

from marketlens.providers.llm.base import LLMError, LLMResponse, LLMUnavailable

# models that support server-side refusal fallbacks
_FALLBACK_MODELS = {"claude-opus-5", "claude-fable-5-1"}
_NO_EFFORT_MODELS = {"claude-haiku-4-5"}


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None, fast_model: str, deep_model: str, client: Any = None) -> None:
        self.fast_model = fast_model
        self.deep_model = deep_model
        self._client = client
        if self._client is None and api_key:
            try:
                import anthropic
            except ImportError as e:  # pragma: no cover - optional dependency
                raise LLMUnavailable("install the 'anthropic' package (pip install marketlens[anthropic])") from e
            self._client = anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=120.0)
        self.available = self._client is not None

    def model_for(self, tier: str) -> str:
        return self.deep_model if tier == "deep" else self.fast_model

    def _effort(self, model: str, tier: str) -> str | None:
        return None if model in _NO_EFFORT_MODELS else ("high" if tier == "deep" else "low")

    def signature(self, tier: str) -> str:
        m = self.model_for(tier)
        return f"anthropic|{m}|effort={self._effort(m, tier)}|fallbacks={m in _FALLBACK_MODELS}"

    def complete_json(self, system: str, user: str, schema: dict[str, Any], tier: str, max_tokens: int = 4000) -> LLMResponse:
        if not self.available:
            raise LLMUnavailable("ANTHROPIC_API_KEY 미설정")
        model = self.model_for(tier)
        output_config: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        effort = self._effort(model, tier)
        if effort is not None:
            output_config["effort"] = effort
        kwargs: dict[str, Any] = dict(
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_config=output_config,
        )
        start = time.perf_counter()
        try:
            if model in _FALLBACK_MODELS:
                resp = self._client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs)
            else:
                resp = self._client.messages.create(**kwargs)
        except Exception as e:  # SDK errors are translated to a typed LLMError for the committee
            raise LLMError(f"{type(e).__name__}") from e
        latency = (time.perf_counter() - start) * 1000
        if getattr(resp, "stop_reason", None) == "refusal":
            raise LLMError("model refused the request")
        if getattr(resp, "stop_reason", None) == "max_tokens":
            raise LLMError("output truncated (max_tokens)")
        text = next((b.text for b in resp.content if getattr(b, "type", "") == "text"), "")
        usage = resp.usage
        return LLMResponse(text, getattr(resp, "model", model), int(usage.input_tokens), int(usage.output_tokens), latency)
