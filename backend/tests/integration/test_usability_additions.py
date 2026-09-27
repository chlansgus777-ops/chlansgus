"""Usability items from the second evaluator's plan: first-run setup without editing files, dollars and shares
instead of "small", the market coverage / missing-data rate of a scan, and a scan that survives an interruption."""

from __future__ import annotations

import json

import pytest

from tests.integration.test_service_api import make_service


# ---------------------------------------------------------------- first-run setup
def test_setup_values_are_validated_before_anything_is_written(tmp_path, monkeypatch):
    from marketlens.config import save_setup

    monkeypatch.setitem(__import__("sys").modules, "keyring", None)  # no OS keychain → the private .env
    env = tmp_path / ".env"
    with pytest.raises(ValueError, match="이름 이메일"):
        save_setup({"SEC_USER_AGENT": "no-email"}, env)
    with pytest.raises(ValueError, match="줄바꿈"):
        save_setup({"FINNHUB_API_KEY": "abc\nMARKETLENS_MODE=LIVE"}, env)  # no injection of other settings
    with pytest.raises(ValueError, match="바꿀 수 없는"):
        save_setup({"MARKETLENS_DATABASE_URL": "sqlite:///x"}, env)
    with pytest.raises(ValueError, match="MOCK, LIVE"):
        save_setup({"MARKETLENS_MODE": "PROD"}, env)
    assert not env.exists()


def test_setup_writes_only_what_was_given_and_keeps_other_lines(tmp_path, monkeypatch):
    from marketlens.config import save_setup

    monkeypatch.setitem(__import__("sys").modules, "keyring", None)
    env = tmp_path / ".env"
    env.write_text('LOG_LEVEL="DEBUG"\nFINNHUB_API_KEY="old"\n', encoding="utf-8")
    where = save_setup({"FINNHUB_API_KEY": "  newkey123  ", "SEC_USER_AGENT": "Hong Gildong hong@example.com", "POLYGON_API_KEY": ""}, env)
    assert where == {"FINNHUB_API_KEY": ".env", "SEC_USER_AGENT": ".env"}  # the empty field is left unchanged
    text = env.read_text(encoding="utf-8")
    assert 'LOG_LEVEL="DEBUG"' in text and 'FINNHUB_API_KEY="newkey123"' in text and "old" not in text
    assert 'SEC_USER_AGENT="Hong Gildong hong@example.com"' in text and "POLYGON" not in text


def test_setup_api_never_returns_the_values(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from marketlens.api.app import create_app

    monkeypatch.setitem(__import__("sys").modules, "keyring", None)
    monkeypatch.setenv("MARKETLENS_ENV_FILE", str(tmp_path / ".env"))
    svc = make_service(universe=40)
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app, headers={"X-MarketLens-Client": "test"}) as c:
        r = c.put("/api/settings/setup", json={"values": {"ALPHAVANTAGE_API_KEY": "SECRETVALUE123"}})
        assert r.status_code == 200 and "SECRETVALUE123" not in r.text and r.json()["restart_required"]
        assert c.put("/api/settings/setup", json={"values": {"SEC_USER_AGENT": "bad"}}).status_code == 422
    with TestClient(app) as c:  # without the client header the CSRF guard refuses the write
        assert c.put("/api/settings/setup", json={"values": {"FRED_API_KEY": "x1234567"}}).status_code == 403
    assert "SECRETVALUE123" in (tmp_path / ".env").read_text(encoding="utf-8")


# ---------------------------------------------------------------- dollars and shares
def test_position_plan_turns_the_action_into_dollars_and_whole_shares():
    from marketlens.domain.portfolio import PortfolioLimits, position_plan

    lim = PortfolioLimits()
    p = position_plan("BUY", None, 100_000.0, 76.51, 74.63, limits=lim)
    assert p is not None and p.size_class == "FULL" and p.shares == 65 and p.amount == pytest.approx(65 * 76.51)
    assert p.risk_amount == pytest.approx(65 * (76.51 - 74.63)) and p.risk_pct == pytest.approx(p.risk_amount / 100_000, abs=1e-4)
    small = position_plan("BUY SMALL", None, 100_000.0, 76.51, 74.63, limits=lim)
    assert small is not None and small.size_class == "HALF" and small.shares == 32
    capped = position_plan("BUY", "SMALL", 100_000.0, 76.51, 74.63, limits=lim)  # the portfolio review's cap wins
    assert capped is not None and capped.size_class == "SMALL" and capped.shares == 16
    room = position_plan("ADD", None, 100_000.0, 100.0, 90.0, current_value=9_000.0, limits=lim)  # 10% cap → $1,000 left
    assert room is not None and room.shares == 10 and room.notes
    assert position_plan("HOLD", None, 100_000.0, 76.5, 74.0) is None
    assert position_plan("BUY", None, None, 76.5, 74.0) is None  # no portfolio value: never guess an account size
    assert position_plan("BUY", "WATCH", 100_000.0, 76.5, 74.0) is None


def test_stock_screen_shows_the_plan_or_asks_for_the_portfolio():
    from fastapi.testclient import TestClient

    from marketlens.api.app import create_app
    from marketlens.infrastructure.db import repository as repo

    svc = make_service(universe=80)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        buy = next(r for r in repo.all_recommendations(s) if r.final_action in ("BUY", "BUY SMALL"))
        t = buy.ticker
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app, headers={"X-MarketLens-Client": "test"}) as c:
        p = c.get(f"/api/stocks/{t}").json()["position_plan"]
        assert p["available"] is False and "포트폴리오" in p["reason"]
        c.put("/api/portfolio", json={"cash": 100000, "holdings": []})
        p = c.get(f"/api/stocks/{t}").json()["position_plan"]
        assert p["available"] is True and p["shares"] > 0 and p["amount"] <= 0.05 * 100000 + 1e-6 and p["nav"] == pytest.approx(100000)


# ---------------------------------------------------------------- coverage and interruption
def test_scan_status_reports_coverage_and_cost():
    svc = make_service(universe=80)
    svc.run_scan(run_committee=False)
    st = svc.scan_status()
    assert st["state"]["status"] == "COMPLETE" and st["state"]["saved"] == st["state"]["total"]
    c = st["coverage"]
    assert c["universe"] == 80 and c["excluded"] + c["deep_analysed"] == 80 and c["analysed"] <= c["deep_analysed"]
    assert 0 <= (c["data_insufficient_rate"] or 0) <= 1 and "llm" in c
    assert not any(k.startswith("주가 ") and k != "주가 기준 미달" for k in c["excluded_by_reason"])  # one reason, not one per price


def test_an_interrupted_scan_keeps_what_it_saved(monkeypatch):
    from marketlens.infrastructure.db import repository as repo

    svc = make_service(universe=80)
    real = svc._persist
    calls = {"n": 0}

    def persist(*a, **k):  # noqa: ANN002, ANN003, ANN202
        calls["n"] += 1
        if calls["n"] == 6:
            raise KeyboardInterrupt  # the app closed mid-scan
        return real(*a, **k)

    monkeypatch.setattr(svc, "_persist", persist)
    with pytest.raises(KeyboardInterrupt):
        svc.run_scan(run_committee=False)
    st = svc.scan_status()
    assert st["state"]["status"] == "INTERRUPTED" and st["state"]["saved"] == 5
    with svc.sf() as s:
        assert len(repo.all_recommendations(s)) == 5  # before: nothing survived an interruption
    assert json.loads(json.dumps(st))  # serialisable for the API
