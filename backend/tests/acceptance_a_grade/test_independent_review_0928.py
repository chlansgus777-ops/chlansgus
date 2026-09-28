"""Independent review counterexamples; expected safe behavior, without modifying project code."""
import sys, os
from pathlib import Path
from datetime import date, timedelta
from types import SimpleNamespace
from dataclasses import replace
import threading

ROOT = Path(os.environ.get('MARKETLENS_REVIEW_ROOT', str(Path(__file__).resolve().parent.parent / 'work/source/chlansgus-claude-marketlens-investment-system-iyki44')))
sys.path.insert(0, str(ROOT / 'backend'))

import pytest
from tests.helpers import card, ctx, plan
from tests.integration.test_service_api import make_service
from marketlens.domain.decision import decide
from marketlens.domain.enums import Action
from marketlens.domain.portfolio import position_plan, portfolio_snapshot, Portfolio, Holding, CandidateProfile, review_candidate
from marketlens.application.committee.orchestrator import apply_committee
from marketlens.application.committee.schemas import PortfolioAdvice
from marketlens.infrastructure.db import repository as repo


def test_half_cap_reaches_amount():
    decision = decide(card(90), plan(), ctx(portfolio_size_cap='HALF'))
    p = position_plan(decision.action.value, decision.size_limit, 100_000, 100, 95)
    assert p.amount <= 2500, (decision.action.value, decision.size_limit, p)


def test_small_cap_reaches_already_small_buy():
    decision = decide(card(74), plan(), ctx(portfolio_size_cap='SMALL'))
    p = position_plan(decision.action.value, decision.size_limit, 100_000, 100, 95)
    assert p.amount <= 1250, (decision.action.value, decision.size_limit, p)


@pytest.mark.parametrize('action', [Action.BUY_SMALL, Action.ADD])
def test_committee_watch_blocks_every_buy(action):
    pm = PortfolioAdvice(portfolio_fit='POOR', suggested_size='WATCH', summary='No new allocation')
    final, _ = apply_committee(action, None, pm, 'FULL')
    assert final not in (Action.BUY, Action.BUY_SMALL, Action.ADD), final


def test_cold_start_preserves_sector_limit():
    svc = make_service(universe=10)
    with svc.sf() as s:
        repo.upsert_holding(s, 'NVDA', 1000, 100)
        repo.set_setting(s, 'portfolio_cash', '100000')
        s.commit()
    with svc.sf() as s:
        before = svc.portfolio(s)
        svc.last_scan_context = svc.scanner(svc.base_cfg, s).build_context(svc.now())
        after = svc.portfolio(s)
    sec = svc.last_scan_context.securities['NVDA']
    candidate = CandidateProfile('OTHER', sec.sector, (), 0)
    cold = review_candidate(before, {'NVDA': 100}, candidate, {})
    warm = review_candidate(after, {'NVDA': 100}, candidate, {})
    assert cold.size_cap == warm.size_cap, (before.holdings[0].sector, after.holdings[0].sector, cold.size_cap, warm.size_cap)


def test_expired_recommendation_has_no_current_buy_quantity():
    from marketlens.api.routes import _position_plan
    svc = make_service(universe=10)
    with svc.sf() as s:
        repo.set_setting(s, 'portfolio_cash', '100000')
        s.commit()
        row = SimpleNamespace(ticker='NVDA', final_action='BUY', result={'decision': {'size_limit': None}})
        summary = {'current_status': 'EXPIRED', 'actionable_now': False, 'price': 100, 'stop': 95}
        p = _position_plan(svc, s, row, summary)
    assert not p['available'], p


def test_new_analysis_refreshes_shared_news_context():
    svc = make_service(universe=10)
    old = SimpleNamespace(as_of=svc.now()-timedelta(days=3), securities={})
    fresh = SimpleNamespace(as_of=svc.now(), securities={})
    svc.last_scan_context = old
    fake = SimpleNamespace(build_context=lambda _: fresh, analyze_single=lambda *a: (object(), object()))
    svc.scanner = lambda *a: fake
    svc.analyze('NVDA', persist=False)
    assert svc.last_scan_context is fresh


def test_sync_refused_while_scan_lock_held():
    svc = make_service(universe=10)
    svc.store = object()
    svc.sync_status = lambda: {}
    done = threading.Event()
    def rounds(*_):
        svc._sync_lock.release()
        done.set()
    svc._sync_rounds = rounds
    svc._lock.acquire()
    try:
        result = svc.start_sync()
        done.wait(2)
    finally:
        svc._lock.release()
    assert result.get('started') is False, result


def test_disjoint_price_days_report_missing_valuation():
    pf = Portfolio((Holding('A', 1, 100, 'Tech'), Holding('B', 1, 100, 'Tech')), 1000)
    snap = portfolio_snapshot(pf, {'A': {date(2026, 9, 23): 100}, 'B': {date(2026, 9, 24): 100}})
    assert set(snap.missing_prices) == {'A','B'}, snap


def test_provider_error_does_not_echo_short_quoted_api_key():
    import httpx
    import json
    from marketlens.providers.live.http import HttpClient
    from marketlens.providers.router import ProviderChain
    from marketlens.providers.contracts import ProviderError
    from marketlens.api.routes import health
    key = 'FAKEKEY1234567890'
    svc = make_service(universe=10)
    http = HttpClient('https://example.invalid', transport=httpx.MockTransport(
        lambda req: httpx.Response(400, json={'api_key': key, 'error': 'invalid key'})))
    provider = SimpleNamespace(name='echo_fixture', mode=svc.mode, configured=True,
                               get_quote=lambda: http.get_json('/quote'))
    chain = ProviderChain('price', [provider], svc.mode, svc.health)
    with pytest.raises(ProviderError):
        chain.call('get_quote')
    req = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(service=svc)))
    payload = health(req)
    assert key not in json.dumps(payload), payload['providers'][-1]
