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


# USD per 1M tokens (input, output) — used for cost *estimates* only
PRICING: dict[str, tuple[float, float]] = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    p = PRICING.get(model)
    if p is None:
        return 0.0
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


class UnavailableLLM:
    name = "none"
    available = False

    def __init__(self, reason: str = "no LLM provider configured") -> None:
        self.reason = reason

    def complete_json(self, system: str, user: str, schema: dict[str, Any], tier: str, max_tokens: int = 4000) -> LLMResponse:
        raise LLMUnavailable(self.reason)
