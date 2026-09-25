"""OpenAI-compatible chat completions (OpenAI, Gemini's OpenAI endpoint, local servers such as Ollama/LM Studio)."""

from __future__ import annotations

import time
from typing import Any

import httpx

from marketlens.providers.llm.base import LLMError, LLMResponse, LLMUnavailable


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, api_key: str | None, fast_model: str, deep_model: str, name: str = "openai_compatible", transport: httpx.BaseTransport | None = None) -> None:
        self.name = name
        self.fast_model = fast_model
        self.deep_model = deep_model
        self.available = bool(base_url)
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._http = httpx.Client(base_url=base_url, headers=headers, timeout=120.0, transport=transport)

    def complete_json(self, system: str, user: str, schema: dict[str, Any], tier: str, max_tokens: int = 4000) -> LLMResponse:
        if not self.available:
            raise LLMUnavailable("no base URL")
        model = self.deep_model if tier == "deep" else self.fast_model
        body = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "output", "strict": True, "schema": schema}},
        }
        start = time.perf_counter()
        try:
            r = self._http.post("/chat/completions", json=body)
        except httpx.HTTPError as e:
            raise LLMError(type(e).__name__) from e
        if r.status_code >= 400:
            raise LLMError(f"HTTP {r.status_code}")
        d = r.json()
        try:
            text = d["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError("malformed completion") from e
        u = d.get("usage", {})
        return LLMResponse(text, d.get("model", model), int(u.get("prompt_tokens", 0)), int(u.get("completion_tokens", 0)), (time.perf_counter() - start) * 1000)
