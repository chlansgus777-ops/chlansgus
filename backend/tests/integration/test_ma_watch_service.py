"""이동평균선 닿음 알림의 대상: the candidate list's top ten in its live order, data-insufficient never — and their lines
come from each one's newest analysis (owner 2026-10-05: first "60점 이상 종목들만", then "상위 10종목만")."""

from marketlens.infrastructure.db import repository as repo
from tests.integration.test_service_api import make_service


def test_only_the_top_ten_candidates_are_watched_for_moving_average_touches():
    svc = make_service(universe=60)
    svc.run_scan(run_committee=False)
    names = svc._ma_alert_names()
    with svc.sf() as s:
        scan = svc.shown_scan(s)
        pool = [r for r in repo.recommendations_for_scan(s, scan.id) if r.rank is not None and r.final_action != "DATA INSUFFICIENT"]
    want = [r.ticker for r in sorted(pool, key=lambda r: (-(r.score or 0), r.rank))][: svc.MA_ALERT_TOP]
    assert len(pool) > svc.MA_ALERT_TOP, "the mock pool is larger than ten"
    assert names == want  # no live price in the test: the stored scores decide the order
    levels = svc._ma_levels()
    assert {lv.symbol for lv in levels} <= set(names) and levels
    with svc.sf() as s:
        for lv in levels:
            r = svc.latest_company_recommendation(s, lv.symbol)
            t = (r.result or {}).get("technicals") or {}
            assert any(abs(lv.sma[n] - t[f"sma{n}"]) < 1e-6 for n in lv.sma)  # no split in the mock: the analysis' own lines


def test_the_live_order_decides_the_top_ten():
    svc = make_service(universe=60)
    svc.run_scan(run_committee=False)
    before = svc._ma_alert_names()
    with svc.sf() as s:
        scan = svc.shown_scan(s)
        low = [r for r in repo.recommendations_for_scan(s, scan.id) if r.rank is not None and r.ticker not in before and r.final_action != "DATA INSUFFICIENT"][-1]
    svc._rejudged[low.id] = {"action": "BUY", "score": 99.0}  # its live re-judgement now scores highest
    assert svc._ma_alert_names()[0] == low.ticker and len(svc._ma_alert_names()) == svc.MA_ALERT_TOP
