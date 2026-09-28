from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


class LLMError(Exception):
    pass


class LLMUnavailable(LLMError):
    pass


@dataclass(frozen=True)
class LLMResponse:
    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float


class LLMProvider(Protocol):
    name: str
    available: bool

    def complete_json(self, system: str, user: str, schema: dict[str, Any], tier: str, max_tokens: int = 4000) -> LLMResponse: ...

    def signature(self, tier: str) -> str:
        """Provider + exact model ID + generation settings for ``tier`` (part of the response-cache key)."""
        ...


# USD per 1M tokens (input, output) — used for cost *estimates* only
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "mock-llm": (0.0, 0.0),  # local deterministic mock: genuinely free
    # OpenAI list prices (USD per 1M input/output tokens) as published in 2025 — verify on openai.com/api/pricing;
    # the app's budget guard uses these, the bill is OpenAI's own
    "gpt-5": (1.25, 10.0),
    "gpt-5-mini": (0.25, 2.0),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-4.1": (2.0, 8.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4o": (2.50, 10.0),
    "gpt-4o-mini": (0.15, 0.60),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float | None:
    """Estimated USD cost, or None when the model's price is unknown (unknown is NOT free)."""
    p = PRICING.get(model)
    if p is None:
        # dated snapshot IDs (e.g. "claude-haiku-4-5-20251001") share the alias' price — the LONGEST matching alias,
        # so "gpt-4o-mini-2024-07-18" is priced as gpt-4o-mini, never as gpt-4o
        keys = sorted((k for k in PRICING if model.startswith(k + "-")), key=len, reverse=True)
        p = PRICING[keys[0]] if keys else None
    if p is None:
        return None
    return round(input_tokens / 1e6 * p[0] + output_tokens / 1e6 * p[1], 6)


_UNSUPPORTED_SCHEMA_KEYS = {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "maxLength", "minLength", "maxItems", "minItems", "title", "default"}


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a Pydantic JSON schema into a structured-output-friendly schema.

    Numeric/length bounds are removed here (they are still enforced by Pydantic validation afterwards).
    """

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out = {k: walk(v) for k, v in node.items() if k not in _UNSUPPORTED_SCHEMA_KEYS}
            if out.get("type") == "object":
                out["additionalProperties"] = False
                if "properties" in out:
                    out["required"] = list(out["properties"].keys())
            return out
        if isinstance(node, list):
            return [walk(x) for x in node]
        return node

    return walk(schema)


class BudgetedLLM:
    """A spending cap in front of a paid provider (e.g. an OpenAI account with a few dollars of credit).

    - Before each call: refused (LLMUnavailable) when the estimated spend has reached ``budget_usd``, or when the
      model's price is unknown (an unknown price cannot be kept under a cap).
    - After each call: its estimated cost is added. The starting spend is this provider's recorded total, so a
      restart does not reset the cap. The estimate uses list prices; the provider's own bill is authoritative."""

    def __init__(self, inner: Any, budget_usd: float, spent_usd: float = 0.0, price: tuple[float, float] | None = None) -> None:
        self.inner = inner
        self.price = price  # (input, output) USD per 1M tokens the user copied from the provider's price page
        self.name = inner.name
        self.budget_usd = budget_usd
        self.spent_usd = spent_usd
        self.available = bool(getattr(inner, "available", False))
        self.reason = getattr(inner, "reason", None)
        import threading

        self._lock = threading.Lock()

    def signature(self, tier: str) -> str:
        return self.inner.signature(tier)

    def model_for(self, tier: str) -> str:
        return self.inner.model_for(tier)

    def remaining(self) -> float:
        return max(0.0, self.budget_usd - self.spent_usd)

    def _cost(self, model: str, tin: int, tout: int) -> float | None:
        if self.price is not None:
            return round(tin / 1e6 * self.price[0] + tout / 1e6 * self.price[1], 6)
        return estimate_cost(model, tin, tout)

    def complete_json(self, system: str, user: str, schema: dict[str, Any], tier: str, max_tokens: int = 4000) -> LLMResponse:
        model = self.inner.model_for(tier) if hasattr(self.inner, "model_for") else ""
        if self._cost(model, 1_000_000, 0) is None:
            raise LLMUnavailable(f"{model}: 단가를 모르는 모델은 예산 한도를 지킬 수 없어 호출하지 않음 — 설정에서 단가를 입력하세요")
        with self._lock:
            if self.spent_usd >= self.budget_usd:
                raise LLMUnavailable(f"AI 예산 소진(추정 ${self.spent_usd:.2f} / 한도 ${self.budget_usd:.2f})")
        resp = self.inner.complete_json(system, user, schema, tier, max_tokens)
        cost = self._cost(resp.model, resp.input_tokens, resp.output_tokens)
        if cost is None:  # the server answered with another model name: charge at the requested model's price
            cost = self._cost(model, resp.input_tokens, resp.output_tokens) or 0.0
        with self._lock:
            self.spent_usd += cost
        return resp


class UnavailableLLM:
    name = "none"
    available = False

    def __init__(self, reason: str = "LLM 제공자 미설정") -> None:
        self.reason = reason

    def signature(self, tier: str) -> str:
        return "none"

    def complete_json(self, system: str, user: str, schema: dict[str, Any], tier: str, max_tokens: int = 4000) -> LLMResponse:
        raise LLMUnavailable(self.reason)
