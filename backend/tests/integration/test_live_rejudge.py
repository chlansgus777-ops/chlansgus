"""Owner 2026-09-29: "시장스캔 버튼이나 자동 스캔이 아니라, 상위 40개 종목이 데이터 부족이 안 뜨게 실시간으로 갱신되길 원해".
The list's top names are analysed again every few seconds with their stored inputs and the live price — no scan and no
provider call — and the list, the stock page's plan and the tick-by-tick verdict follow."""

from __future__ import annotations

from datetime import timedelta

from marketlens.application.data_access import Fetched
from tests.integration.test_transactions import client  # noqa: F401


def _scan_without_prices(c, svc):
    real = svc.data.quote
    svc.data.quote = lambda t: Fetched(None, None, "시험: 가격 공급자 응답 없음")  # as when the rate limit refused every request
    try:
        assert c.post("/api/scan?committee=false").status_code == 200
    finally:
        svc.data.quote = real


def test_price_less_rows_are_judged_again_on_the_live_price(client):  # noqa: F811
    c, svc = client
    _scan_without_prices(c, svc)
    rows = c.get("/api/opportunities").json()["rows"]
    assert rows and all(r["action"] == "DATA INSUFFICIENT" for r in rows)  # the owner's screen
    now = svc.now()
    for r in rows:  # the live feed prints a price for every listed name (Toss, 1 s)
        close = r["price"] or 100.0
        svc.quotes.ingest_poll(r["ticker"], float(close), now - timedelta(seconds=2), "toss")
    assert svc.live_rejudge() == len(rows)
    after = c.get("/api/opportunities").json()["rows"]
    assert all(r.get("live_at") for r in after)
    assert sum(r["action"] != "DATA INSUFFICIENT" for r in after) >= len(after) // 2
    for r in after:
        assert r["price_source"] == "toss" and r["stored"]["action"] == "DATA INSUFFICIENT"
    # the same prints again: nothing recomputed
    assert svc.live_rejudge() == 0


def test_no_fresh_price_keeps_the_stored_row(client):  # noqa: F811
    c, svc = client
    _scan_without_prices(c, svc)
    assert svc.live_rejudge() == 0
    assert all(not r.get("live_at") for r in c.get("/api/opportunities").json()["rows"])


def test_a_held_name_outside_the_list_and_the_stock_page_are_live_too(client):  # noqa: F811
    """Owner 2026-09-29: "모든 정보를 실시간으로" — the names held, watched or on screen are re-judged every round, and the
    stock page reads its own live judgement."""
    c, svc = client
    assert c.post("/api/scan?committee=false").status_code == 200
    listed = {r["ticker"] for r in c.get("/api/opportunities").json()["rows"]}
    other = next(sec.ticker for sec in svc.data.securities().value if sec.ticker not in listed and not sec.is_etf)
    h = {"X-MarketLens-Client": "test"}
    assert c.post(f"/api/stocks/{other}/analysis", headers=h).status_code == 202
    svc.analyses.wait(f"analysis:{other}", 10)
    assert c.get(f"/api/stocks/{other}").status_code == 200
    c.put("/api/portfolio", json={"holdings": [{"ticker": other, "quantity": 5, "cost_basis": 50}]})
    assert c.get(f"/api/stocks/{other}/live").json()["live"] is None  # no live price yet
    svc.quotes.ingest_poll(other, 123.45, svc.now() - timedelta(seconds=1), "toss")
    svc._pool_cache = None  # the holding just changed (the pool is rebuilt every 30 s in the app)
    svc.live_rejudge()
    live = c.get(f"/api/stocks/{other}/live").json()["live"]
    assert live and live["price"] == 123.45 and live["action"] and live["analysed_at"]


def test_any_price_change_is_judged_again_and_a_cut_round_resumes_with_the_rest(client):  # noqa: F811
    """Owner 2026-09-29 ("9800X3D"): the whole pool every second, on any change of the price; on a slower PC a round
    stops at its time budget and the names it did not reach go first the next second."""
    c, svc = client
    assert c.post("/api/scan?committee=false").status_code == 200
    rows = c.get("/api/opportunities").json()["rows"]
    now = svc.now()
    for r in rows:
        svc.quotes.ingest_poll(r["ticker"], float(r["price"] or 100.0), now - timedelta(seconds=1), "toss")
    assert svc.live_rejudge() == len(rows)
    for r in rows:  # one cent moved (well under the old 0.05 % floor)
        svc.quotes.ingest_poll(r["ticker"], float(r["price"] or 100.0) + 0.01, now, "toss")
    svc.LIVE_ROUND_BUDGET = 0.0  # a very slow PC: one name, then the budget is spent
    first = svc.live_rejudge()
    assert 1 <= first < len(rows)
    svc.LIVE_ROUND_BUDGET = 60.0
    assert svc.live_rejudge() == len(rows) - first  # exactly the ones left, none twice
