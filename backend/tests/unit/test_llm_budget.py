"""Paid-AI safety (2026-09-28): a small OpenAI credit is protected by a spending cap; automatic scans skip the
committee; the paid API always uses its official address. Offline — no request reaches OpenAI."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from marketlens.providers.llm.base import BudgetedLLM, LLMUnavailable, estimate_cost
from marketlens.providers.llm.openai_compat import OpenAICompatibleProvider


def fake_openai(seen: list[dict], model: str = "gpt-4o-mini-2024-07-18", pin: int = 100_000, pout: int = 1_000) -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        seen.append({"url": str(req.url), "body": body, "auth": req.headers.get("authorization")})
        return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": "{}"}}], "usage": {"prompt_tokens": pin, "completion_tokens": pout}})

    return httpx.MockTransport(handler)


def test_openai_prices_are_known_for_dated_snapshots():
    assert estimate_cost("gpt-4o-mini-2024-07-18", 1_000_000, 1_000_000) == pytest.approx(0.75)
    assert estimate_cost("gpt-4.1-mini", 1_000_000, 0) == pytest.approx(0.40)


def test_budget_stops_calls_once_the_estimate_reaches_the_cap():
    seen: list[dict] = []
    inner = OpenAICompatibleProvider("https://api.openai.com/v1", "sk-test", "gpt-4o-mini", "gpt-4o-mini", name="openai", transport=fake_openai(seen))
    llm = BudgetedLLM(inner, budget_usd=0.05)
    per_call = estimate_cost("gpt-4o-mini", 100_000, 1_000)  # 0.0156
    calls = 0
    with pytest.raises(LLMUnavailable, match="예산 소진"):
        for _ in range(10):
            llm.complete_json("s", "u", {"type": "object"}, "fast")
            calls += 1
    assert calls == 4 and len(seen) == 4  # 3 × 0.0156 < 0.05 ≤ 4 × 0.0156: the 5th is refused before any request
    assert llm.spent_usd == pytest.approx(4 * per_call)
    assert seen[0]["body"]["max_completion_tokens"] == 4000 and "max_tokens" not in seen[0]["body"]


def test_budget_refuses_a_model_with_an_unknown_price():
    seen: list[dict] = []
    inner = OpenAICompatibleProvider("https://api.openai.com/v1", "sk-test", "some-new-model", "some-new-model", name="openai", transport=fake_openai(seen))
    with pytest.raises(LLMUnavailable, match="단가"):
        BudgetedLLM(inner, 5.0).complete_json("s", "u", {}, "fast")
    assert seen == []


def test_budget_starts_from_the_recorded_spend():
    inner = OpenAICompatibleProvider("https://api.openai.com/v1", "k", "gpt-4o-mini", "gpt-4o-mini", name="openai", transport=fake_openai([]))
    with pytest.raises(LLMUnavailable):
        BudgetedLLM(inner, 8.0, spent_usd=8.0).complete_json("s", "u", {}, "fast")


def test_paid_openai_needs_a_key_and_a_cap_and_uses_the_official_address(tmp_path):
    from marketlens.application.services import OPENAI_URL, build_llm
    from marketlens.config import load_settings
    from marketlens.domain.enums import DataMode

    base = replace(load_settings(), mode=DataMode.LIVE, database_url=f"sqlite:///{tmp_path / 'x.db'}", llm_provider="openai",
                   openai_base_url="http://127.0.0.1:11434/v1", fast_model="gpt-4o-mini", deep_model="gpt-4.1-mini")
    assert not build_llm(replace(base, openai_api_key=None, llm_budget_usd=8.0)).available
    no_cap = build_llm(replace(base, openai_api_key="sk-x", llm_budget_usd=0.0))
    assert not no_cap.available and "예산" in no_cap.reason
    ok = build_llm(replace(base, openai_api_key="sk-x", llm_budget_usd=8.0))
    assert isinstance(ok, BudgetedLLM) and ok.available and ok.budget_usd == 8.0 and ok.spent_usd == 0.0
    assert ok.inner.signature("fast").startswith(f"openai|{OPENAI_URL}|")  # never the saved local URL


def test_automatic_scans_skip_the_committee_unless_opted_in():
    from datetime import datetime, timezone
    from types import SimpleNamespace

    from marketlens.workers.scheduler import BackgroundScheduler

    got = []
    svc = SimpleNamespace(settings=SimpleNamespace(scan_interval_minutes=60, ai_committee_on_schedule=False),
                          run_scan=lambda run_committee: got.append(run_committee), now=lambda: None, store=None)
    sch = BackgroundScheduler(svc)
    sch.step(datetime(2026, 9, 30, 14, 0, tzinfo=timezone.utc))  # regular session
    assert got == [False]
    svc.settings.ai_committee_on_schedule = True
    sch._last_scan = None
    sch.step(datetime(2026, 9, 30, 15, 0, tzinfo=timezone.utc))
    assert got == [False, True]


def test_setup_accepts_a_budget_and_rejects_nonsense():
    from marketlens.config import validate_setup

    assert validate_setup({"LLM_BUDGET_USD": "8", "LLM_PROVIDER": "openai", "AI_COMMITTEE_ON_SCHEDULE": "0"})["LLM_BUDGET_USD"] == "8"
    for bad in ("0", "-1", "abc", "5000", "1e3"):
        with pytest.raises(ValueError):
            validate_setup({"LLM_BUDGET_USD": bad})


def test_a_user_entered_price_lets_a_new_model_run_under_the_cap():
    """A model the app has no price for (e.g. a newer release) runs only with the user's price from the provider's
    page, and is charged at that price."""
    seen: list[dict] = []
    inner = OpenAICompatibleProvider("https://api.openai.com/v1", "k", "gpt-6-luna", "gpt-6-luna", name="openai", transport=fake_openai(seen, model="gpt-6-luna"))
    llm = BudgetedLLM(inner, 1.0, price=(0.10, 0.50))
    llm.complete_json("s", "u", {}, "fast")
    assert llm.spent_usd == pytest.approx(100_000 / 1e6 * 0.10 + 1_000 / 1e6 * 0.50)
    from marketlens.config import validate_setup

    assert validate_setup({"LLM_PRICE_INPUT_PER_M": "0.10", "LLM_PRICE_OUTPUT_PER_M": "0.5"})["LLM_PRICE_INPUT_PER_M"] == "0.10"
    with pytest.raises(ValueError):
        validate_setup({"LLM_PRICE_INPUT_PER_M": "0"})
