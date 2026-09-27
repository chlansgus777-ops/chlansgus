"""독립 평가(대상 2240992, 70점 C)의 반례. 원본: evaluations/independent_2240992 (평가자 작성, 수정 없이 옮김). Expected-correct-behaviour tests. Failures demonstrate defects in 2240992.
No network, secrets, source changes or actual trading required.
"""
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from marketlens.domain.decision import decide
from marketlens.domain.enums import Action, DataQuality, Exchange
from marketlens.domain.freshness import PlanCheck, recommendation_freshness
from marketlens.domain.guidance import extract
from marketlens.domain.market import Bar, Security
from marketlens.domain.earnings import pair_with_releases
from marketlens.domain.what_changed import AnalysisDigest, diff, material_reasons
from marketlens.application.pipeline import earnings_visible
from marketlens.application.market_store import MarketStore
from marketlens.infrastructure.db.models import Base
from marketlens.infrastructure.db.session import make_engine, make_session_factory
from tests.helpers import card, ctx, plan

UTC = timezone.utc


def store():
    engine = make_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    return MarketStore(make_session_factory(engine), 'LIVE')


def security(t='ABC', cik=111):
    return Security(t, 'Example', Exchange.NASDAQ, 'Technology', 'Software', 1e10, cik=cik)


def test_r1_material_gate_cannot_reinstate_buy_above_max_price():
    # Formula-consistent plan: max buy=(120+2*90)/3=100; price 100.1 => RR<2.
    p = plan(price=100.1, max_buy=100, stop=90, t1=120)
    ts = datetime(2026, 9, 25, 15, tzinfo=UTC)
    prev = AnalysisDigest('ABC', ts - timedelta(minutes=1), 85, {}, 'BUY', 99.9, True, False, (120-99.9)/(99.9-90),
                          None, None, None, None, (), (), None, None, False, stop=90, max_buy=100, atr=2)
    cur = replace(prev, as_of=ts, price=100.1, in_buy_zone=False, rr=p.rr_at_current, action=None)
    material = material_reasons(diff(prev, cur))
    assert material == (), material
    d = decide(card(85), p, ctx(previous_action=Action.BUY, material_changes=material))
    assert d.raw_action == Action.WAIT
    assert d.action not in {Action.BUY, Action.BUY_SMALL, Action.ADD}, d


def test_r2_same_timestamp_quote_must_validate_plan():
    ts = datetime(2026, 9, 25, 15, tzinfo=UTC)
    r = recommendation_freshness(ts, 'FRESH', ts, plan=PlanCheck(100.1, 100, 90, 120, bullish=True),
                                quote_price=100.1, quote_ts=ts)
    assert not r.actionable, r


def test_r3_after_hours_release_not_visible_before_actual_release():
    released = datetime(2026, 7, 30, 21, tzinfo=UTC)  # 17:00 NY
    reports = pair_with_releases([{'period': date(2026, 6, 30), 'actual': 2, 'estimate': 1, 'quarter': 2, 'year': 2026}], [released], 'fixture')
    assert len(reports) == 1
    assert not earnings_visible(reports[0], datetime(2026, 7, 30, 20, 1, tzinfo=UTC))


def test_r4_guidance_range_preserves_each_endpoint_scale():
    g = extract('Revenue for the third quarter of fiscal 2026 is expected to be $900 million to $1.1 billion.')[0]
    assert g.status != 'EXTRACTED' or (g.low, g.high) == (900_000_000, 1_100_000_000), g


def test_r5_trading_security_missing_once_is_not_delisted():
    st = store()
    st.sync_universe([security(), security('KEEP', 333)], date(2026, 9, 21))
    st.save_bars('ABC', [Bar(date(2026, 9, 22), 100, 102, 99, 101, 1e6)], 'polygon')
    st.sync_universe([security('KEEP', 333)], date(2026, 9, 22))
    from marketlens.infrastructure.db import repository as repo
    with st.sf() as s:
        assert repo.delisted_on(s, 'ABC', 'LIVE') is None


def test_r6_reused_ticker_does_not_archive_new_company_bars():
    st = store()
    st.sync_universe([security(cik=111)], date(2026, 9, 21))
    st.save_bars('ABC', [Bar(date(2026, 9, 21), 100, 101, 99, 100, 1e6)], 'polygon')
    # MarketSync.run fetches new grouped bars BEFORE resolving ticker identity.
    st.save_bars('ABC', [Bar(date(2026, 9, 22), 10, 11, 9, 10, 1e6)], 'polygon')
    st.sync_universe([security(cik=222)], date(2026, 9, 22))
    old = st.bars('ABC~111', date(2026, 9, 21), date(2026, 9, 22))
    assert all(b.day < date(2026, 9, 22) for b in old), old
    new = st.bars('ABC', date(2026, 9, 22), date(2026, 9, 22))
    assert len(new) == 1 and new[0].close == 10


def test_r7_store_must_check_requested_start_not_only_end():
    from marketlens.application.data_access import DataAccess
    st = store()
    st.sync_universe([security()], date(2026, 9, 21))
    old = Bar(date(2026, 9, 1), 90, 91, 89, 90, 1e6)
    latest = Bar(date(2026, 9, 25), 100, 101, 99, 100, 1e6)
    st.save_bars('ABC', [latest], 'polygon')
    class Chain:
        def call(self, *args, **kwargs):
            return SimpleNamespace(value=[old, latest], provider='fixture', conflicts=[])
    reg = SimpleNamespace(chain=lambda _: Chain())
    data = DataAccess(reg, {}, store=st)
    result = data.bars('ABC', old.day, latest.day)
    assert result.value[0].day == old.day, result


def test_r8_small_allocation_must_still_respect_sector_limit():
    from marketlens.domain.portfolio import Portfolio, Holding, CandidateProfile, PortfolioLimits, review_candidate
    limits = PortfolioLimits()
    pf = Portfolio((Holding('OLD', 295, 100, 'Technology'),), 70500)
    review = review_candidate(pf, {'OLD': 100}, CandidateProfile('NEW', 'Technology', (), 0), {}, limits)
    sizes = {'FULL': limits.full_position, 'HALF': limits.half_position, 'SMALL': limits.small_position, 'WATCH': 0}
    proposed = sizes[review.size_cap.value]
    assert review.sector_weights['Technology'] + proposed <= limits.max_sector, review


def test_r9_guidance_must_compare_the_named_quarter():
    from marketlens.application.estimate_book import attach_guidance
    from marketlens.domain.estimates import EstimateObservation
    from marketlens.domain.earnings import EarningsReport
    # Q1 report gives Q3 guidance. The earliest upcoming report is Q2, a different fiscal period.
    filed = datetime(2026, 4, 30, 20, 30, tzinfo=UTC)
    g = SimpleNamespace(status='EXTRACTED', filed_at=filed, accession='A', metric='revenue', period_label='third quarter of fiscal 2026',
                        low=200, high=220, source_url='https://example.test', sentence='Q3 revenue guidance', confidence='MEDIUM')
    consensus = EstimateObservation('ABC', 'finnhub', 'FQ2026Q2', 'quarter', None, date(2026, 4, 29), eps=1, revenue=100,
                                   report_date=date(2026, 7, 30))
    rep = EarningsReport(date(2026, 4, 30), 'Q1 2026', 'fixture', eps_actual=1, eps_consensus=1)
    result = attach_guidance([rep], [g], [consensus], date(2026, 5, 1))[0]
    assert result.guidance.next_q_revenue_consensus is None, result.guidance


def test_r10_original_stop_survives_an_intermediate_hold():
    from marketlens.application.pipeline import run_analysis
    from tests.integration.test_service_api import make_service
    svc = make_service(universe=80)
    with svc.sf() as session:
        result, inputs = svc.scanner(svc.base_cfg, session).analyze_single('NVDA', svc.now())
    # A real holding was bought at 110 with original stop 100. Yesterday's HOLD
    # should not erase this stop; today's completed close 95 has breached it.
    bars = tuple(replace(b, open=95, high=96, low=94, close=95) for b in inputs.bars)
    prev = replace(result.digest, action='HOLD', as_of=inputs.as_of - timedelta(days=2), stop=100, price=110)
    inp = replace(inputs, held=True, bars=bars, quote=replace(inputs.quote, price=95), previous=prev, previous_action=Action.HOLD)
    outcome = run_analysis(inp, svc.base_cfg)
    assert outcome.decision.action == Action.SELL, outcome.decision


def test_r11_previous_recommendation_must_match_security_identity():
    from tests.integration.test_service_api import make_service
    svc = make_service(universe=80)
    svc.analyze('NVDA', run_committee=False, persist=True)
    # Existing recommendation belongs to CIK 111; the same ticker is now CIK 222.
    st = MarketStore(svc.sf, 'MOCK')
    st.sync_universe([security('NVDA', 111)], date(2026, 9, 25))
    st.sync_universe([security('NVDA', 222)], date(2026, 9, 28))
    svc.store = st
    svc.data.store = st
    with svc.sf() as s:
        digest, action = svc._previous_lookup(s)('NVDA', datetime(2026, 9, 28, 15, tzinfo=UTC))
    assert digest is None and action is None, (digest, action)


def test_r12_neutral_macro_news_cannot_invent_rate_increase():
    from marketlens.application.issue_engine import build_issues
    from marketlens.providers.contracts import NewsItem
    ts = datetime(2026, 9, 25, 15, tzinfo=UTC)
    item = NewsItem('n1', ts, 'Federal Reserve leaves interest rates unchanged', 'Policy remains unchanged.', 'https://example.test/1', 'fixture', 'OFFICIAL', ())
    result = build_issues([item], ts)
    effects = [e for issue in result.issues for e in issue.primary_effects if e.node_id == 'MACRO:RATES']
    assert not effects or all(e.direction == 0 for e in effects), effects


def test_r13_stock_split_must_not_trigger_old_stop():
    from marketlens.application.pipeline import run_analysis
    from marketlens.domain.corporate_actions import SplitEvent
    from tests.integration.test_service_api import make_service
    svc = make_service(universe=80)
    with svc.sf() as session:
        result, inp = svc.scanner(svc.base_cfg, session).analyze_single('NVDA', svc.now())
    bars = tuple(replace(b, open=100, high=101, low=99, close=100) for b in inp.bars)
    prev = replace(result.digest, action='BUY', as_of=inp.as_of - timedelta(days=2), stop=900, price=1000)
    inp = replace(inp, held=True, bars=bars, quote=replace(inp.quote, price=100), previous=prev, previous_action=Action.BUY,
                  splits=(SplitEvent('NVDA', date(2026, 9, 24), 1, 10, 'fixture'),))
    out = run_analysis(inp, svc.base_cfg)
    assert not any('직전 추천의 손절 기준가 아래' in r for r in out.decision.reasons), out.decision


def test_r14_prospective_eps_adjusts_when_split_occurs_after_snapshot():
    from marketlens.application.data_access import DataAccess
    from marketlens.domain.estimates import EstimateObservation
    from marketlens.domain.corporate_actions import SplitEvent
    st = store()
    st.sync_universe([security()], date(2026, 9, 23))
    st.save_estimates([EstimateObservation('ABC', 'alphavantage', 'FY2026', 'annual', date(2026, 12, 31), date(2026, 9, 23), eps=10)])
    st.save_splits([SplitEvent('ABC', date(2026, 9, 24), 1, 10, 'fixture')])
    out = DataAccess(SimpleNamespace(), {}, store=st).estimates('ABC', date(2026, 9, 25)).value
    # Reject stale-basis estimates or normalize to $1; never forward EPS $10 next to post-split prices.
    assert out is None or out.forward_eps is None or out.forward_eps == 1, out


def test_r15_cross_site_get_cannot_trigger_persistent_analysis():
    from fastapi.testclient import TestClient
    from sqlalchemy import select, func
    from marketlens.api.app import create_app
    from marketlens.infrastructure.db.models import RecommendationRow
    from tests.integration.test_service_api import make_service
    svc = make_service(universe=80)
    app = create_app(svc.settings, service=svc, run_migrations=False)
    # A browser's no-cors image/navigation GET can omit Origin and custom client headers.
    with svc.sf() as s:
        before = s.scalar(select(func.count()).select_from(RecommendationRow))
    with TestClient(app) as client:
        client.get('/api/stocks/NVDA?refresh=true', headers={'Sec-Fetch-Site': 'cross-site', 'Sec-Fetch-Mode': 'no-cors'})
    with svc.sf() as s:
        after = s.scalar(select(func.count()).select_from(RecommendationRow))
    assert after == before, f'Cross-site GET created {after-before} recommendation(s)'
