"""Round 10 feature a): holdings from trade records, through the service and the API (docs/design/TRANSACTION_LEDGER.md).
The pure rules are in tests/invariants/test_ledger_invariants.py; here: the records win over the entered line, a store
split applies, the company (not the ticker label) owns the records, failures are refused with a reason, concurrent
submissions cannot both pass the holding check."""

from __future__ import annotations

import threading
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from marketlens.domain.corporate_actions import SplitEvent
from marketlens.domain.ledger import LedgerError
from tests.integration.test_service_api import NOW, make_service

H = {"X-MarketLens-Client": "test"}


@pytest.fixture()
def client():  # noqa: ANN201
    svc = make_service(universe=40)
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app, headers=H) as c:
        yield c, svc


def _holding(c: TestClient, ticker: str) -> dict | None:
    return next((h for h in c.get("/api/portfolio").json()["holdings"] if h["ticker"] == ticker), None)


def test_the_records_decide_the_holding_and_failures_are_refused(client):
    c, _svc = client
    d = NOW.date() - timedelta(days=20)
    assert c.post("/api/transactions", json={"ticker": "nvda", "day": d.isoformat(), "kind": "BUY", "quantity": 10, "price": 100, "fees": 2}).status_code == 200
    c.put("/api/portfolio", json={"holdings": [{"ticker": "NVDA", "quantity": 999, "cost_basis": 1}]})  # an entered line for the same company
    h = _holding(c, "NVDA")
    assert h["source"] == "ledger" and h["quantity"] == 10 and h["cost_basis"] == pytest.approx(100.2)
    r = c.post("/api/transactions", json={"ticker": "NVDA", "day": (d + timedelta(days=1)).isoformat(), "kind": "SELL", "quantity": 11, "price": 120})
    assert r.status_code == 400 and "보유" in r.json()["detail"]
    r = c.post("/api/transactions", json={"ticker": "NVDA", "day": (NOW.date() + timedelta(days=3)).isoformat(), "kind": "BUY", "quantity": 1, "price": 1})
    assert r.status_code == 400 and "미래" in r.json()["detail"]
    for bad in ({"kind": "BUY", "quantity": 0, "price": 1}, {"kind": "BUY", "quantity": 1, "price": -1}, {"kind": "HOLD", "quantity": 1, "price": 1},
                {"kind": "DIVIDEND", "amount": 0}, {"kind": "SPLIT", "split_from": 0, "split_to": 2}):
        assert c.post("/api/transactions", json={"ticker": "NVDA", "day": d.isoformat()} | bad).status_code in (400, 422), bad
    r = c.post("/api/transactions", json={"ticker": "NVDA", "day": (d + timedelta(days=2)).isoformat(), "kind": "SELL", "quantity": 4, "price": 120, "fees": 1})
    assert r.status_code == 200 and isinstance(r.json()["added"], int)
    body = c.get("/api/transactions").json()
    pos = body["securities"][0]["position"]
    assert pos["quantity"] == 6 and pos["realized_pnl"] == pytest.approx(4 * (120 - 100.2) - 1)
    first = body["securities"][0]["trades"][0]["id"]
    r = c.delete(f"/api/transactions/{first}")
    assert r.status_code == 400 and "매도" in r.json()["detail"]
    assert c.delete("/api/transactions/99999").status_code == 404
    assert _holding(c, "NVDA")["quantity"] == 6


def test_a_fully_sold_company_leaves_the_holdings_but_keeps_its_result(client):
    c, _svc = client
    d = NOW.date() - timedelta(days=20)
    c.post("/api/transactions", json={"ticker": "AMD", "day": d.isoformat(), "kind": "BUY", "quantity": 5, "price": 10})
    c.post("/api/transactions", json={"ticker": "AMD", "day": (d + timedelta(days=1)).isoformat(), "kind": "SELL", "quantity": 5, "price": 12})
    c.post("/api/transactions", json={"ticker": "AMD", "day": (d + timedelta(days=2)).isoformat(), "kind": "DIVIDEND", "amount": 3})
    assert _holding(c, "AMD") is None
    t = c.get("/api/transactions").json()
    assert t["realized_pnl"] == pytest.approx(10.0) and t["dividends"] == pytest.approx(3.0)


def test_two_sales_submitted_together_cannot_both_pass(client):
    _c, svc = client
    d = NOW.date() - timedelta(days=10)
    svc.add_transaction("NVDA", d, "BUY", 10, 50.0)
    go = threading.Barrier(2)
    results: list[str] = []

    def sell() -> None:
        go.wait()
        try:
            svc.add_transaction("NVDA", d + timedelta(days=1), "SELL", 10, 60.0)
            results.append("ok")
        except LedgerError:
            results.append("refused")

    ts = [threading.Thread(target=sell) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(timeout=30)
    assert sorted(results) == ["ok", "refused"]
    assert svc._ledger_lock.acquire(blocking=False)
    svc._ledger_lock.release()
    with svc.sf() as s:
        assert svc.ledger(s)[0]["position"].quantity == 0


# ------------------------------------------------------------- the store's splits and the company (LIVE store)
from tests.invariants.test_identity import D1, D2, D3, D4, world  # noqa: E402,F401  (the rename / reuse / relist world)


def test_a_store_split_applies_to_the_records_across_a_rename(world):  # noqa: F811
    svc, _ = world
    svc.store.save_splits([SplitEvent("NEW", D3, 1, 2, "polygon")])
    svc.add_transaction("OLD", D1, "BUY", 10, 10.0)  # bought as OLD, before the rename and the split
    svc.add_transaction("NEW", D3, "BUY", 2, 6.0)  # the execution day: already on the new basis
    with svc.sf() as s:
        pf = svc.portfolio(s)
        g = svc.ledger(s)
    h = next(h for h in pf.holdings if h.ticker == "NEW")
    assert h.source == "ledger" and h.quantity == 22 and h.cost_basis == pytest.approx(112.0 / 22)
    assert len(g) == 1 and g[0]["ticker"] == "NEW" and g[0]["position"].splits_applied == (f"{D3.isoformat()}:1:2",)


def test_records_of_a_reused_ticker_stay_with_the_old_company(world):  # noqa: F811
    svc, _ = world
    svc.add_transaction("ABC", D1, "BUY", 5, 20.0)  # company B (CIK 2)
    svc.add_transaction("ABC", D3, "BUY", 3, 6.0)  # company C (CIK 3) now uses ABC
    with pytest.raises(LedgerError, match="보유"):
        svc.add_transaction("ABC", D4, "SELL", 4, 7.0)  # C holds 3 — B's 5 are not C's
    with svc.sf() as s:
        pf = svc.portfolio(s)
        g = {x["security"]: x for x in svc.ledger(s)}
    abc = [h for h in pf.holdings if h.ticker == "ABC"]
    assert len(abc) == 1 and abc[0].quantity == 3
    assert g["sec:ABC~2"]["ticker"] is None and g["sec:ABC~2"]["position"].quantity == 5  # B's line was archived as ABC~2
    assert any("ABC" in n and "재사용" in n for n in pf.notes)


def test_the_buy_amount_counts_the_holding_from_the_records():
    """Limits and the buy amount use the holding the records give (the one Service.portfolio)."""
    from marketlens.api.routes import _position_plan, _row_summary
    from marketlens.infrastructure.db import repository as repo
    from marketlens.infrastructure.db.models import RecommendationRow

    svc = make_service(universe=60)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        repo.set_setting(s, "portfolio_cash", "100000")
        s.commit()
        row = next(r for r in s.query(RecommendationRow).all() if r.final_action in ("BUY", "BUY SMALL") and r.price)
        summary = _row_summary(row, svc)  # what the API passes: a quantity only for a recommendation current now (review 2026-09-28 F04)
        assert summary["actionable_now"] is True and summary["price"] == row.price
        before = _position_plan(svc, s, row, summary)
    assert before["available"]
    held = int(0.08 * before["nav"] / row.price)  # 8% of the account already held, bought through the records
    svc.add_transaction(row.ticker, NOW.date() - timedelta(days=30), "BUY", held, row.price)
    with svc.sf() as s:
        after = _position_plan(svc, s, row, summary)
        pf = svc.portfolio(s)
    assert next(h for h in pf.holdings if h.ticker == row.ticker).source == "ledger"
    if after["available"]:
        assert held * row.price + after["amount"] <= 0.10 * after["nav"] + 1e-6
    assert (after.get("amount") or 0) < before["amount"]


# ------------------------------------------------------------- round 10 code review findings (written before the fix)
def test_two_share_classes_are_two_holdings(tmp_path):
    from marketlens.domain.market import Bar
    from tests.invariants.test_identity import _sec, _svc

    svc = _svc(tmp_path)
    svc.store.save_grouped(D1, {"BRK-A": Bar(D1, 700000, 700000, 700000, 700000, 1e3), "BRK-B": Bar(D1, 470, 470, 470, 470, 1e6)}, "polygon")
    svc.store.sync_universe([_sec("BRK-A", 1067983), _sec("BRK-B", 1067983)], D1)
    svc.add_transaction("BRK-A", D1, "BUY", 1, 700000.0)
    svc.add_transaction("BRK-B", D1, "BUY", 10, 470.0)
    with pytest.raises(LedgerError, match="보유"):
        svc.add_transaction("BRK-A", D2, "SELL", 5, 700000.0)  # 1 BRK-A held; BRK-B's 10 are another security
    with svc.sf() as s:
        pf = svc.portfolio(s)
    got = {h.ticker: (h.quantity, round(h.cost_basis)) for h in pf.holdings}
    assert got == {"BRK-A": (1, 700000), "BRK-B": (10, 470)}


def test_a_dividend_record_does_not_hide_the_entered_holding(client):
    c, _svc = client
    c.put("/api/portfolio", json={"holdings": [{"ticker": "NVDA", "quantity": 100, "cost_basis": 50}]})
    c.post("/api/transactions", json={"ticker": "NVDA", "day": (NOW.date() - timedelta(days=3)).isoformat(), "kind": "DIVIDEND", "amount": 4})
    h = _holding(c, "NVDA")
    assert h is not None and h["quantity"] == 100 and h["source"] == "manual"
    assert c.get("/api/transactions").json()["dividends"] == pytest.approx(4.0)


def test_an_overridden_entered_line_is_said(client):
    c, _svc = client
    c.put("/api/portfolio", json={"holdings": [{"ticker": "AMD", "quantity": 7, "cost_basis": 50}]})
    d = NOW.date() - timedelta(days=9)
    c.post("/api/transactions", json={"ticker": "AMD", "day": d.isoformat(), "kind": "BUY", "quantity": 2, "price": 10})
    c.post("/api/transactions", json={"ticker": "AMD", "day": (d + timedelta(days=1)).isoformat(), "kind": "SELL", "quantity": 2, "price": 11})
    pf = c.get("/api/portfolio").json()
    assert _holding(c, "AMD") is None  # the records say: sold
    assert any("AMD" in n and "수동 입력 줄" in n for n in pf["notes"])


def test_records_only_under_the_old_name_are_labelled_with_the_new_one(world):  # noqa: F811
    svc, _ = world
    svc.add_transaction("OLD", D1, "BUY", 10, 10.0)  # renamed NEW on D2; nothing recorded under NEW
    with svc.sf() as s:
        pf = svc.portfolio(s)
    assert [(h.ticker, h.quantity) for h in pf.holdings] == [("NEW", 10)]


def test_an_entered_line_of_a_reused_ticker_is_not_valued_as_the_new_company(world):  # noqa: F811
    svc, _ = world
    from marketlens.infrastructure.db.models import HoldingRow
    from marketlens.domain.market_calendar import NY
    from datetime import datetime, time

    with svc.sf() as s:  # entered while ABC was company B (before C listed as ABC on D2)
        s.add(HoldingRow(ticker="ABC", quantity=5, cost_basis=20.0, updated_at=datetime.combine(D1, time(12), tzinfo=NY)))
        s.commit()
    svc.add_transaction("ABC", D3, "BUY", 3, 6.0)  # company C
    with svc.sf() as s:
        pf = svc.portfolio(s)
    assert [(h.ticker, h.quantity, h.source) for h in pf.holdings if h.ticker == "ABC"] == [("ABC", 3, "ledger")]
    assert any("재사용" in n for n in pf.notes)


def test_deleting_a_missing_record_is_404_by_type(client):
    c, _svc = client
    assert c.delete("/api/transactions/424242").status_code == 404
