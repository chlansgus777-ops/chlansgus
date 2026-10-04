"""이동평균선 닿음 알림의 대상: only names whose newest analysis scores 60 or more — held, watched or in the shown scan's
analysed pool — and their lines come from that analysis (owner 2026-10-05: "60점 이상 종목들만")."""

from tests.integration.test_service_api import make_service


def test_only_names_scoring_60_or_more_are_watched_for_moving_average_touches():
    svc = make_service(universe=60)
    svc.run_scan(run_committee=False)
    levels = svc._ma_levels()
    assert levels, "the mock pool has names scoring 60+"
    with svc.sf() as s:
        for lv in levels:
            r = svc.latest_company_recommendation(s, lv.symbol)
            assert r is not None and r.score >= 60
            t = (r.result or {}).get("technicals") or {}
            assert any(abs(lv.sma[n] - t[f"sma{n}"]) < 1e-6 for n in lv.sma)  # no split in the mock: the analysis' own lines
        scan = svc.shown_scan(s)
        pool = [r for r in __import__("marketlens.infrastructure.db.repository", fromlist=["x"]).recommendations_for_scan(s, scan.id)
                if r.rank is not None and r.rank <= svc.LIVE_POOL]
    low = {r.ticker for r in pool if r.score < 60}
    assert low, "the mock pool also has names under 60"
    assert not low & {lv.symbol for lv in levels}
