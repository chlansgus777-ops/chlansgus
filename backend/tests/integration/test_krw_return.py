"""/api/portfolio ``krw``: a ledger holding is split with its purchase-day rates, an entered line is unknown with its reason,
and the parts add up to the won P&L."""

from __future__ import annotations

from datetime import timedelta

from tests.integration.test_transactions import client  # noqa: F401
from tests.integration.test_service_api import NOW


def test_ledger_holding_split_and_entered_line_unknown(client):  # noqa: F811
    c, _svc = client
    d = NOW.date() - timedelta(days=60)
    assert c.post("/api/transactions", json={"ticker": "NVDA", "day": d.isoformat(), "kind": "BUY", "quantity": 10, "price": 150}).status_code == 200
    c.post("/api/transactions", json={"ticker": "NVDA", "day": (d + timedelta(days=5)).isoformat(), "kind": "SELL", "quantity": 4, "price": 160})
    c.put("/api/portfolio", json={"holdings": [{"ticker": "MSFT", "quantity": 2, "cost_basis": 300}]})
    k = c.get("/api/portfolio").json()["krw"]
    rows = {r["ticker"]: r for r in k["rows"]}
    n = rows["NVDA"]
    assert n["known"] and n["buy_fx"] > 1000 and n["stock_krw"] + n["fx_krw"] == n["total_krw"] == n["value_krw"] - n["cost_krw"]
    assert not rows["MSFT"]["known"] and "직접 입력" in rows["MSFT"]["reason"]
    assert k["totals"]["count"] == 1 and k["fx_now"] and k["fx_source"]
