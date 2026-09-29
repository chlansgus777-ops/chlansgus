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
