"""Follow-ups to the independent review of 2240992 (F01–F14) beyond the reviewer's own tests: the behaviour that must
not change while those defects were fixed, and the edges of each fix."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

UTC = timezone.utc


def _store():  # noqa: ANN202
    from marketlens.application.market_store import MarketStore
    from marketlens.infrastructure.db.models import Base
    from marketlens.infrastructure.db.session import make_engine, make_session_factory

    eng = make_engine("sqlite:///:memory:")
    Base.metadata.create_all(eng)
    return MarketStore(make_session_factory(eng), "LIVE")


# ---------------------------------------------------------------- F01: a plan that holds is still actionable
def test_a_plan_that_holds_at_its_own_price_and_the_quote_stays_current():
    from marketlens.domain.freshness import PlanCheck, recommendation_freshness

    ts = datetime(2026, 9, 25, 15, tzinfo=UTC)
    r = recommendation_freshness(ts, "FRESH", ts, plan=PlanCheck(99.0, 100, 90, 120, bullish=True), quote_price=99.0, quote_ts=ts)
    assert r.actionable and r.status == "CURRENT", r
    bad = recommendation_freshness(ts, "FRESH", ts, plan=PlanCheck(100.1, 100, 90, 120, bullish=True))  # no quote at all
    assert not bad.actionable and bad.status == "PLAN_INVALIDATED"


# ---------------------------------------------------------------- F02: which stop a HOLD watches
def test_a_hold_watches_the_carried_stop_first_then_its_own_and_nothing_when_not_held():
    from marketlens.application.pipeline import watched_stop
    from marketlens.domain.what_changed import AnalysisDigest

    def d(guard: float | None) -> AnalysisDigest:
        return AnalysisDigest(ticker="T", as_of=datetime(2026, 6, 1, 20, tzinfo=UTC), score=70.0, action="HOLD", price=100.0, in_buy_zone=False,
                              stop_breached=False, rr=None, eps_revision_30d=None, revenue_revision_30d=None, last_earnings_date=None,
                              guidance_signature=None, issue_ids=(), major_issue_ids=(), regime=None, us10y=None, thesis_invalidated=False,
                              components={}, stop=85.0, guard_stop=guard)

    assert watched_stop(d(92.0), held=True) == 92.0
    assert watched_stop(d(None), held=True) == 85.0
    assert watched_stop(d(None), held=False) is None


# ---------------------------------------------------------------- F06: bars before the reuse stay with the old company
def test_a_reuse_archives_only_the_bars_before_the_new_company_was_listed():
    from marketlens.domain.enums import Exchange
    from marketlens.domain.market import Bar, Security

    st = _store()
    sec = lambda cik: Security("ABC", "Example", Exchange.NASDAQ, "Technology", "Software", 1e10, cik=cik)  # noqa: E731
    st.sync_universe([sec(111)], date(2026, 9, 18))
    st.save_bars("ABC", [Bar(date(2026, 9, 18), 100, 100, 100, 100, 1e6), Bar(date(2026, 9, 21), 101, 101, 101, 101, 1e6)], "polygon")
    st.save_bars("ABC", [Bar(date(2026, 9, 22), 10, 10, 10, 10, 1e6)], "polygon")
    st.sync_universe([sec(222)], date(2026, 9, 22))
    assert [b.day for b in st.bars("ABC~111", date(2026, 9, 1), date(2026, 9, 30))] == [date(2026, 9, 18), date(2026, 9, 21)]
    assert [b.close for b in st.bars("ABC", date(2026, 9, 1), date(2026, 9, 30))] == [10]


# ---------------------------------------------------------------- F07: gaps are filled only while the backfill runs
def test_a_complete_backfill_or_a_scan_never_asks_the_provider_for_the_front():
    from marketlens.application.data_access import DataAccess
    from marketlens.domain.enums import Exchange
    from marketlens.domain.market import Bar, Security

    st = _store()
    st.sync_universe([Security("ABC", "Example", Exchange.NASDAQ, "Technology", "Software", 1e10, cik=1)], date(2026, 9, 21))
    latest = Bar(date(2026, 9, 25), 100, 101, 99, 100, 1e6)
    st.save_bars("ABC", [latest], "polygon")
    calls: list[object] = []

    class Chain:
        def call(self, *a: object, **k: object) -> SimpleNamespace:
            calls.append(a)
            return SimpleNamespace(value=[Bar(date(2026, 9, 1), 90, 91, 89, 90, 1e6), latest], provider="fixture", conflicts=[])

    data = DataAccess(SimpleNamespace(chain=lambda _: Chain()), {}, store=st)
    assert data.bars("ABC", date(2026, 9, 1), latest.day, fill_gaps=False).value == [latest] and not calls  # a scan
    st.set_setting("bars_backfill_complete", "2025-11-29")
    assert data.bars("ABC", date(2026, 9, 1), latest.day).value == [latest] and not calls  # not trading earlier


def test_the_sync_records_a_complete_backfill_only_when_every_day_was_answered():
    from marketlens.application.sync import MarketSync
    from marketlens.domain.market import Bar

    class G:
        name, configured = "polygon", True

        def get_grouped_daily(self, d: date) -> dict:
            return {"TTT": Bar(d, 5, 5, 5, 5, 1e6)}

    class C:
        def __init__(self, p: list) -> None:
            self.providers = p

        def call(self, *_a: object) -> SimpleNamespace:
            return SimpleNamespace(value=[])

    st = _store()
    reg = SimpleNamespace(chain=lambda k: C([G()]) if k == "price" else C([]))
    MarketSync(reg, st).run(datetime(2026, 6, 3, 21, tzinfo=UTC), backfill_days=30, max_bar_calls=5)  # type: ignore[arg-type]
    assert st.get_setting("bars_backfill_complete") is None  # 5 of ~21 sessions
    for _ in range(5):
        MarketSync(reg, st).run(datetime(2026, 6, 3, 21, tzinfo=UTC), backfill_days=30, max_bar_calls=5)  # type: ignore[arg-type]
    assert st.get_setting("bars_backfill_complete") == "2026-05-04"


# ---------------------------------------------------------------- F08: sizing by the room left
def test_sector_room_gives_the_largest_size_that_fits():
    from marketlens.domain.portfolio import CandidateProfile, Holding, Portfolio, PortfolioLimits, SizeClass, review_candidate

    lim = PortfolioLimits()
    for tech, expect in ((270, SizeClass.HALF), (250, SizeClass.FULL), (285, SizeClass.SMALL), (295, SizeClass.WATCH)):
        pf = Portfolio((Holding("OLD", tech, 100, "Technology"),), 100_000 - tech * 100)
        r = review_candidate(pf, {"OLD": 100}, CandidateProfile("NEW", "Technology", (), 0), {}, lim)
        assert r.size_cap == expect, (tech, r.size_cap)


# ---------------------------------------------------------------- F09: the named quarter matches
def test_guidance_for_the_next_quarter_keeps_its_consensus():
    from marketlens.application.estimate_book import attach_guidance
    from marketlens.domain.earnings import EarningsReport
    from marketlens.domain.estimates import EstimateObservation

    filed = datetime(2026, 4, 30, 20, 30, tzinfo=UTC)
    g = SimpleNamespace(status="EXTRACTED", filed_at=filed, accession="A", metric="revenue", period_label="second quarter of fiscal 2026",
                        low=200, high=220, source_url="https://example.test", sentence="Q2 revenue guidance", confidence="MEDIUM")
    cons = EstimateObservation("ABC", "finnhub", "FQ2026Q2", "quarter", None, date(2026, 4, 29), eps=1, revenue=205, report_date=date(2026, 7, 30))
    rep = EarningsReport(date(2026, 4, 30), "Q1 2026", "fixture", eps_actual=1, eps_consensus=1)
    (out,) = attach_guidance([rep], [g], [cons], date(2026, 5, 1))
    assert out.guidance is not None and out.guidance.next_q_revenue_consensus == 205
    g2 = SimpleNamespace(**{**g.__dict__, "period_label": "second quarter of fiscal 2027"})  # same quarter, another year
    (out,) = attach_guidance([rep], [g2], [cons], date(2026, 5, 1))
    assert out.guidance.next_q_revenue_consensus is None


# ---------------------------------------------------------------- F10: the same company keeps its history
def test_the_same_company_still_finds_its_previous_recommendation():
    from marketlens.application.market_store import MarketStore
    from marketlens.domain.enums import Exchange
    from marketlens.domain.market import Security
    from tests.integration.test_service_api import make_service

    svc = make_service(universe=80)
    svc.analyze("NVDA", run_committee=False, persist=True)
    st = MarketStore(svc.sf, "MOCK")
    st.sync_universe([Security("NVDA", "Example", Exchange.NASDAQ, "Technology", "Software", 1e10, cik=111)], date(2026, 9, 25))
    svc.store = st
    with svc.sf() as s:
        digest, action = svc._previous_lookup(s)("NVDA", datetime(2026, 9, 28, 15, tzinfo=UTC))
    assert digest is not None and action is not None


# ---------------------------------------------------------------- F11: the move words decide the direction
@pytest.mark.parametrize("title,expect", [
    ("Treasury yields climb as inflation data runs hot", 1.0),
    ("Federal Reserve cuts interest rates", -1.0),
    ("Treasury yields fall as inflation cools", -1.0),
    ("Federal Reserve holds rates steady", None),
    ("Oil prices jump after supply disruption", 1.0),
    ("Oil prices tumble on demand worries", -1.0),
])
def test_rates_and_oil_follow_the_stated_move(title, expect):
    from marketlens.application.issue_engine import build_issues
    from marketlens.providers.contracts import NewsItem

    ts = datetime(2026, 9, 25, 15, tzinfo=UTC)
    res = build_issues([NewsItem("n1", ts, title, title, "https://example.test/1", "fixture", "OFFICIAL", ())], ts)
    eff = [e for i in res.issues for e in i.primary_effects if e.node_id in ("MACRO:RATES", "COMMODITY:OIL")]
    if expect is None:
        assert not eff or all(e.direction == 0 for e in eff), eff
    else:
        assert eff and all((e.direction > 0) == (expect > 0) for e in eff), eff


# ---------------------------------------------------------------- F14: the app's own requests still pass
def test_same_origin_and_the_desktop_shell_still_reach_the_api():
    from fastapi.testclient import TestClient

    from marketlens.api.app import create_app
    from tests.integration.test_service_api import make_service

    svc = make_service(universe=40)
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app) as c:
        assert c.get("/api/system", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
        assert c.get("/api/system", headers={"Sec-Fetch-Site": "cross-site", "Origin": "tauri://localhost"}).status_code == 200
        assert c.get("/api/system").status_code == 200  # no fetch metadata (CLI, tests)
        assert c.get("/api/system", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
        assert c.get("/api/system", headers={"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"}).status_code == 403


_ = timedelta
