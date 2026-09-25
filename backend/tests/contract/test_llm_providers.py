import json
from types import SimpleNamespace

import httpx
import pytest

from marketlens.application.committee.schemas import AgentReport
from marketlens.providers.llm.anthropic_provider import AnthropicProvider
from marketlens.providers.llm.base import LLMError, LLMUnavailable, estimate_cost, strict_schema
from marketlens.providers.llm.openai_compat import OpenAICompatibleProvider

OUT = json.dumps({"agent": "macro", "stance": "neutral", "confidence": 50, "summary": "x"})


class FakeMessages:
    def __init__(self, stop="end_turn"):
        self.kwargs = None
        self.stop = stop

    def create(self, **kw):
        self.kwargs = kw
        return SimpleNamespace(stop_reason=self.stop, model=kw["model"], content=[SimpleNamespace(type="text", text=OUT)], usage=SimpleNamespace(input_tokens=100, output_tokens=20))


class FakeClient:
    def __init__(self, stop="end_turn"):
        self.messages = FakeMessages(stop)
        self.beta = SimpleNamespace(messages=FakeMessages(stop))


def test_strict_schema_forbids_extra_fields():
    s = strict_schema(AgentReport.model_json_schema())
    assert s["additionalProperties"] is False and "maximum" not in json.dumps(s)
    assert set(s["required"]) == set(s["properties"])


def test_anthropic_structured_output_and_tiers():
    c = FakeClient()
    p = AnthropicProvider(None, "claude-haiku-4-5", "claude-opus-5", client=c)
    r = p.complete_json("sys", "user", {"type": "object"}, "fast")
    assert r.text == OUT and r.model == "claude-haiku-4-5"
    kw = c.messages.kwargs
    assert kw["output_config"]["format"]["type"] == "json_schema" and "effort" not in kw["output_config"]
    p.complete_json("sys", "user", {"type": "object"}, "deep")
    bk = c.beta.messages.kwargs
    assert bk["model"] == "claude-opus-5" and bk["fallbacks"] == "default" and bk["output_config"]["effort"] == "high"


def test_anthropic_refusal_and_missing_key():
    with pytest.raises(LLMError):
        AnthropicProvider(None, "claude-haiku-4-5", "claude-opus-5", client=FakeClient("refusal")).complete_json("s", "u", {}, "fast")
    p = AnthropicProvider(None, "a", "b")
    assert not p.available
    with pytest.raises(LLMUnavailable):
        p.complete_json("s", "u", {}, "fast")


def test_openai_compatible():
    def h(req):
        body = json.loads(req.content)
        assert body["response_format"]["type"] == "json_schema"
        return httpx.Response(200, json={"model": body["model"], "choices": [{"message": {"content": OUT}}], "usage": {"prompt_tokens": 5, "completion_tokens": 6}})

    p = OpenAICompatibleProvider("http://local/v1", None, "small", "big", transport=httpx.MockTransport(h))
    r = p.complete_json("s", "u", {"type": "object"}, "deep")
    assert r.text == OUT and r.model == "big" and r.input_tokens == 5
    bad = OpenAICompatibleProvider("http://local/v1", None, "s", "b", transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    with pytest.raises(LLMError):
        bad.complete_json("s", "u", {}, "fast")


def test_cost_estimate():
    assert estimate_cost("claude-opus-5", 1_000_000, 1_000_000) == 30.0
    assert estimate_cost("unknown", 10, 10) == 0.0
