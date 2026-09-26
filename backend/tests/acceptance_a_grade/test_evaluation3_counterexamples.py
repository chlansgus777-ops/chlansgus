"""3차 독립 평가 (대상 커밋 445221f) — 새 반례. 원본: evaluations/eval3 (평가자 작성, 수정 없이 옮김).

각 테스트는 입력과 기대 동작을 그대로 적었다. 445221f 에서는 모두 실패하고, 고친 뒤에는 통과해야 한다.
실행: 저장소 루트에서  python -m pytest -q -o addopts="" evaluations/eval3
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from marketlens.application.data_access import FUNDAMENTALS, DataAccess, TTLCache
from marketlens.application.market_store import MarketStore
from marketlens.domain.corporate_actions import SplitEvent, normalize_quarters
from marketlens.domain.enums import DataMode, Exchange
from marketlens.domain.fundamentals import QuarterlyFinancials, as_of, compute_metrics
from marketlens.domain.market import Bar, Security
from marketlens.domain.market_calendar import is_trading_day
from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import Base, PaperPositionRow, PriceBarRow, RecommendationRow
from marketlens.infrastructure.db.session import make_engine, make_session_factory

UTC = timezone.utc


def _store(tmp_path) -> MarketStore:
    eng = make_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    Base.metadata.create_all(eng)
    return MarketStore(make_session_factory(eng), "LIVE")


def _sec(t: str, cik: int | None, name: str = "Co", exch: Exchange = Exchange.NASDAQ) -> Security:
    return Security(t, name, exch, "Unknown", "Unknown", None, cik=cik)


def _bars(st: MarketStore, t: str, start: date, n: int, px: float) -> None:
    st.save_bars(t, [Bar(start + timedelta(days=i), px, px + 1, px - 1, px, 1e6) for i in range(n)], "polygon")


def _sessions(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


# ============================================================================ N1 (P0) guidance sign, reversed
POSITIVE_WITH_UNRELATED_LOSS = [
    ("For the full year 2027, we expect adjusted diluted EPS of $2.40 to $2.50, which excludes the loss on the sale of our European business.", "eps", 2.40, 2.50),
    ("For fiscal 2027, the Company expects diluted EPS of $1.10 to $1.20, compared with a net loss of $0.50 per diluted share in fiscal 2026.", "eps", 1.10, 1.20),
    ("For the full year 2027, we expect EPS of $4.10 to $4.30, reflecting higher expected credit losses.", "eps", 4.10, 4.30),
    ("For the full year 2027, we expect operating margin of 12% to 13%, excluding restructuring losses.", "operating_margin", 0.12, 0.13),
]


@pytest.mark.parametrize("sentence, metric, lo, hi", POSITIVE_WITH_UNRELATED_LOSS)
def test_N1_positive_guidance_next_to_an_unrelated_loss_is_never_stored_negative(sentence, metric, lo, hi):
    """445221f: any 'loss' word + no profit word → the guided numbers are negated and stored EXTRACTED."""
    from marketlens.domain.guidance import extract

    (item,) = extract(sentence)
    assert item.metric == metric
    # acceptable: the positive range, or GUIDANCE_UNCLEAR (value None) — never a negative EXTRACTED value
    assert item.status == "GUIDANCE_UNCLEAR" or (abs(item.low - lo) < 1e-9 and abs(item.high - hi) < 1e-9), (item.low, item.high, item.status)


def test_N1_negative_without_a_number_right_after_it_is_not_read_as_positive():
    from marketlens.domain.guidance import extract

    (item,) = extract("For the full year 2027, we expect EPS to be negative, in the range of $0.10 to $0.15.")
    assert item.status == "GUIDANCE_UNCLEAR" or (item.low is not None and item.low < 0), (item.low, item.high, item.status)


def test_N1_end_to_end_in_line_guidance_is_not_a_weak_guide(tmp_path):
    """Guidance $1.10–$1.20 vs pre-release consensus $1.14 is in line. 445221f: stored −1.20..−1.10 →
    guide_eps_vs_cons −200.9% → BEAT_WEAK_GUIDE (earnings quality 0.75 → 0.40, and −200% goes to the AI committee)."""
    from marketlens.application.estimate_book import attach_guidance
    from marketlens.domain.earnings import EarningsReport, ResultQuality, assess_earnings
    from marketlens.domain.estimates import EstimateObservation
    from marketlens.domain.guidance import extract

    st = _store(tmp_path)
    filed = datetime(2026, 11, 5, 21, tzinfo=UTC)
    text = ("Fourth quarter outlook. For the fourth quarter of fiscal 2026, we expect diluted EPS of $1.10 to $1.20, "
            "excluding the expected loss on the sale of our European business.")
    st.save_guidance("ABC", "0000-26-000001", filed, "https://www.sec.gov/x", extract(text))
    st.save_estimates([EstimateObservation("ABC", "finnhub", "2027Q1", "quarter", None, date(2026, 11, 1), eps=1.14, report_date=date(2027, 2, 5))])
    reports = [EarningsReport(date(2026, 11, 5), "Q3 FY2026", "finnhub", revenue_actual=1.02e9, revenue_consensus=1.0e9, eps_actual=1.05, eps_consensus=1.00)]
    rep = attach_guidance(reports, st.guidance("ABC", datetime(2026, 11, 6, tzinfo=UTC)), st.estimate_history("ABC", date(2026, 11, 6)), date(2026, 11, 6))
    a = assess_earnings(rep)
    assert a.result_quality != ResultQuality.BEAT_WEAK_GUIDE, (a.guide_eps_vs_cons, a.result_quality)


# ============================================================================ N2 (P1) rename into a known ticker
def test_N2_rename_into_a_ticker_once_used_by_a_delisted_company(tmp_path):
    """SBC Communications (CIK 732717) renamed itself T in 2005; T had belonged to AT&T Corp (CIK 5907, delisted).
    Expected: the old AT&T Corp rows are archived AND SBC→T is a rename (history kept, not a delisting).
    445221f: REUSE only — SBC is recorded DELISTED on the rename day and T has no price history."""
    st = _store(tmp_path)
    st.sync_universe([_sec("T", 5907, "AT&T Corp", Exchange.NYSE), _sec("SBC", 732717, "SBC Communications", Exchange.NYSE)], date(2005, 6, 1))
    _bars(st, "SBC", date(2005, 6, 1), 100, 24.0)
    st.sync_universe([_sec("SBC", 732717, "SBC Communications", Exchange.NYSE)], date(2005, 11, 21))  # AT&T Corp delisted
    st.sync_universe([_sec("T", 732717, "AT&T Inc.", Exchange.NYSE)], date(2005, 12, 1))  # SBC → T
    with st.sf() as s:
        assert repo.delisted_on(s, "SBC", "LIVE") is None, "a rename was recorded as a delisting"
    assert len(st.bars("T", date(2005, 1, 1), date(2005, 11, 30))) == 100, "the renamed company lost its price history"


def test_N2_same_day_ticker_swap_between_two_listed_issuers(tmp_path):
    """Issuer 1 (FB→META) takes the ticker that issuer 2 gives up the same day (META→METV)."""
    st = _store(tmp_path)
    d1, d2 = date(2026, 6, 1), date(2026, 6, 10)
    st.sync_universe([_sec("FB", 1326801, "Meta Platforms"), _sec("META", 1839486, "Issuer Two")], d1)
    _bars(st, "FB", d1 - timedelta(days=30), 30, 300.0)
    _bars(st, "META", d1 - timedelta(days=30), 30, 15.0)
    st.sync_universe([_sec("META", 1326801, "Meta Platforms"), _sec("METV", 1839486, "Issuer Two")], d2)
    with st.sf() as s:
        assert repo.delisted_on(s, "FB", "LIVE") is None, "Meta Platforms recorded as delisted"
    meta, metv = st.bars("META", d1 - timedelta(days=40), d2), st.bars("METV", d1 - timedelta(days=40), d2)
    assert len(meta) == 30 and meta[0].close == 300.0, "issuer 1 lost its history"
    assert len(metv) == 30 and metv[0].close == 15.0, "issuer 2 lost its history"


# ============================================================================ N3 (P1) rename, then the old ticker is reused
def test_N3_old_ticker_reused_after_a_rename_never_leaks_into_the_renamed_company(tmp_path):
    st = _store(tmp_path)
    d1, d2, d3 = date(2026, 6, 1), date(2026, 6, 10), date(2026, 8, 3)
    st.sync_universe([_sec("FB", 1326801, "Meta Platforms")], d1)
    _bars(st, "FB", d1 - timedelta(days=30), 30, 300.0)
    st.sync_universe([_sec("META", 1326801, "Meta Platforms")], d2)  # rename
    _bars(st, "META", d2, 20, 310.0)
    st.sync_universe([_sec("META", 1326801, "Meta Platforms"), _sec("FB", 999, "FB NewCo")], d3)  # the free ticker is reused
    _bars(st, "FB", d3, 5, 2.0)
    st.save_splits([SplitEvent("FB", d3 + timedelta(days=2), 10, 1, "polygon")])  # NewCo's 1-for-10 reverse split

    splits = st.splits("META")
    assert splits == [], f"another issuer's split is applied to META: {splits}"
    q = QuarterlyFinancials(date(2026, 6, 30), date(2026, 7, 30), "Q2", "sec-edgar", revenue=4e10, eps_diluted=2.0)
    eps = normalize_quarters([q], splits, d3 + timedelta(days=5))[0][0].eps_diluted
    assert eps == 2.0, f"META EPS becomes {eps} (P/E ÷10) through the pipeline's normalize_quarters"
    meta = st.bars("META", d1 - timedelta(days=40), d3 + timedelta(days=10))
    assert meta[0].close == 300.0 and len([b for b in meta if b.close == 2.0]) == 0, "pre-rename history lost / NewCo bars merged"
    listed = sorted(s.ticker for s in st.securities(d2 + timedelta(days=5)))
    assert listed == ["META"], f"one company listed twice on one day: {listed}"


# ============================================================================ N4 (P1) split after a rename
def test_N4_split_after_a_rename_rescales_the_predecessor_bars(tmp_path):
    st = _store(tmp_path)
    d1, d2 = date(2026, 6, 1), date(2026, 6, 10)
    st.sync_universe([_sec("AAA", 42)], d1)
    _bars(st, "AAA", d1 - timedelta(days=20), 20, 100.0)
    st.sync_universe([_sec("BBB", 42)], d2)  # rename AAA → BBB
    _bars(st, "BBB", d2, 10, 100.0)
    split_day = d2 + timedelta(days=10)
    with st.sf() as s:  # every stored row was retrieved before the split
        for r in s.query(PriceBarRow):
            r.retrieved_at = datetime(2026, 6, 1, tzinfo=UTC)
        s.commit()
    st.save_splits([SplitEvent("BBB", split_day, 1, 2, "polygon")])
    st.adjust_bars_for_splits(split_day)
    _bars(st, "BBB", split_day, 3, 50.0)
    closes = {b.close for b in st.bars("BBB", d1 - timedelta(days=30), split_day + timedelta(days=5))}
    assert closes == {50.0}, f"pre-rename bars kept the pre-split basis → a fake −50% at the rename: {sorted(closes)}"


# ============================================================================ N5 (P1) paper account after a ticker reuse
def _rec(s, rid: int, at: datetime) -> None:
    s.add(RecommendationRow(id=rid, ticker="ABC", as_of=at, mode="LIVE", price_quality="FRESH", score=85, confidence=70, deterministic_action="BUY",
                            final_action="BUY", sector="Tech", sector_model="generic", regime="r", data_quality="OK", result={}, inputs={},
                            model_config_snapshot={}, input_fingerprint="x", scoring_model_version="s", decision_model_version="d",
                            agent_prompt_version="a", provider_version="p", config_version="c", schema_version="1", created_at=at))


def _pos(s, rid: int, at: datetime, stop: float, t1: float, t2: float, mb: float) -> None:
    s.add(PaperPositionRow(recommendation_id=rid, ticker="ABC", recommended_at=at, status="PENDING", score=85, confidence=70, action="BUY", regime="r",
                           sector="Tech", stop=stop, target1=t1, target2=t2, max_buy=mb, notional=10000, thesis="t", model_version="m", updated_at=at))


def test_N5_ticker_reuse_does_not_rewrite_the_paper_account(tmp_path):
    """Old Co (CIK 111) BUY on 2026-05-01 opens 05-04 @ ~50; Old Co delisted 06-01; New Co (CIK 222) takes ABC 08-03
    and gets a BUY on 08-20. 445221f: the re-simulation turns the May trade back into PENDING and never opens
    the August one (bars are grouped by ticker, and the archive key 'ABC~111' is requested from the provider)."""
    from marketlens.application.evaluation_service import EvaluationService
    from marketlens.domain.paper import PaperConfig

    st = _store(tmp_path)

    class PolygonLike:  # knows only the current listing of a ticker; "ABC~111" is not a ticker
        def call(self, method, t, start, end, cross_check=None):  # noqa: ANN001, ANN201
            return SimpleNamespace(value=[] if "~" in t else st.bars(t, start, end), provider="polygon", conflicts=[])

    reg = SimpleNamespace(chain=lambda kind: PolygonLike())

    def run(now: datetime) -> None:
        svc = SimpleNamespace(sf=st.sf, data=DataAccess(reg, {}, store=st), mode=DataMode.LIVE, base_cfg=SimpleNamespace(paper=PaperConfig()), now=lambda: now)
        EvaluationService(svc).update_paper(now)

    def positions() -> dict[int, PaperPositionRow]:
        with st.sf() as s:
            return {p.id: p for p in s.query(PaperPositionRow)}

    st.sync_universe([_sec("ABC", 111, "Old Co")], date(2026, 3, 2))
    st.save_bars("ABC", [Bar(d, 50, 51, 49, 50 + 0.1 * i, 1e6) for i, d in enumerate(_sessions(date(2026, 3, 2), date(2026, 5, 29)))], "polygon")
    may = datetime(2026, 5, 1, 15, tzinfo=UTC)
    with st.sf() as s:
        _rec(s, 1, may)
        s.flush()
        _pos(s, 1, may, 45.0, 60.0, 70.0, 52.0)
        s.commit()
    run(datetime(2026, 5, 29, 21, tzinfo=UTC))
    assert positions()[1].status == "OPEN" and positions()[1].entry_day == date(2026, 5, 4)  # holds on 445221f

    st.sync_universe([], date(2026, 6, 1))
    st.sync_universe([_sec("ABC", 222, "New Co")], date(2026, 8, 3))
    st.save_bars("ABC", [Bar(d, 20, 20.5, 19.5, 20, 1e6) for d in _sessions(date(2026, 8, 3), date(2026, 9, 25))], "polygon")
    aug = datetime(2026, 8, 20, 15, tzinfo=UTC)
    with st.sf() as s:
        _rec(s, 2, aug)
        s.flush()
        _pos(s, 2, aug, 18.0, 26.0, 30.0, 21.0)
        s.commit()
    run(datetime(2026, 9, 25, 21, tzinfo=UTC))
    p = positions()
    assert p[1].status in ("OPEN", "CLOSED") and p[1].entry_day == date(2026, 5, 4), f"Old Co trade rewritten to {p[1].status}"
    assert p[2].status in ("OPEN", "CLOSED") and p[2].entry_day == date(2026, 8, 21), f"New Co trade never opened ({p[2].status})"


# ============================================================================ N6 (P2) readiness gate denominator
def test_N6_parser_gaps_are_not_excluded_from_fundamentals_coverage(tmp_path):
    """Five large 10-Q filers; four tag revenue/net income with concepts outside the fallback list, so the real
    parser raises NotSupported('분기 10-Q/10-K 데이터 없음'). 445221f: 1/5 covered → progress 1.0 → SCANNER_READY."""
    import json

    from marketlens.application.readiness import evaluate
    from marketlens.application.registry import build_live_registry
    from marketlens.application.sync import MarketSync
    from marketlens.config import Settings
    from marketlens.providers.live.sec_edgar import parse_company_facts
    from tests.acceptance_a_grade.test_ingestion_manifest import _reg

    def q(val, end, start, filed):  # noqa: ANN001, ANN202
        return {"val": val, "end": end, "start": start, "filed": filed, "form": "10-Q"}

    periods = [("2026-03-31", "2026-01-01", "2026-05-01"), ("2026-06-30", "2026-04-01", "2026-08-01")]
    good = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [q(1e9, *p) for p in periods]}}}}}
    odd = {"facts": {"us-gaap": {"RevenueFromContractWithCustomerIncludingAssessedTax": {"units": {"USD": [q(1e9, *p) for p in periods]}},
                                 "ProfitLoss": {"units": {"USD": [q(1e8, *p) for p in periods]}}}}}

    class ParserSEC:
        name, mode, configured = "sec-edgar", DataMode.LIVE, True

        def get_quarterly(self, t):  # noqa: ANN001, ANN201
            return parse_company_facts(good if t == "A" else odd, t)

    st = _store(tmp_path)
    today = date(2026, 9, 24)
    st.sync_universe([Security(t, t, Exchange.NASDAQ, "Tech", "x", None) for t in "ABCDE"], today)
    for t in "ABCDE":
        st.save_bars(t, [Bar(today - timedelta(days=i), 100, 101, 99, 100, 1e6) for i in range(10)], "polygon")
    st.set_shares({t: (5e7, today) for t in "ABCDE"})
    st.refresh_market_caps()
    rep = SimpleNamespace(errors=[], fundamentals_ingested=0, fundamentals_failed=0, fundamentals_pending=0)
    sec = ParserSEC()
    MarketSync(_reg(sec), st)._ingest_fundamentals(sec, datetime(2026, 9, 25, 15, tzinfo=UTC), rep, 10, timedelta(days=7), 1e9, 2e7, today)
    stats = st.coverage_stats(today, 1e9)
    stats.update(market_days=250, bars_60=stats["listed"], bars_200=stats["listed"], bars_240=stats["listed"], large_with_sector=stats["large"], estimate_history_days=10)
    reg = build_live_registry(Settings(mode=DataMode.LIVE, database_url="sqlite:///:memory:", sec_user_agent="t t@example.com", llm_provider="none"))
    r = evaluate("LIVE", reg, stats, json.dumps({"status": "SYNC_COMPLETE"}))
    assert r.progress["fundamentals"] <= 0.2 + 1e-9 and r.scanner_status == "SCANNER_NOT_READY", (r.progress["fundamentals"], r.scanner_status)


# ============================================================================ N7 (P2) manifest back-off vs the TTL cache
def test_N7_a_retry_after_the_back_off_is_a_real_request(tmp_path):
    """445221f (production cache TTL for fundamentals = 24h): after the 1h back-off the 'retry' is served from the
    cached failure — no request, yet attempts 1→5 and next attempt pushed to t+36h. The existing test hides this
    by replacing the cache before retrying."""
    from marketlens.providers.contracts import ProviderUnavailable
    from tests.acceptance_a_grade.test_ingestion_manifest import FakeSEC, _reg

    now0 = datetime(2026, 9, 25, 15, tzinfo=UTC)
    clock = [now0]
    sec = FakeSEC({"BAD": ProviderUnavailable("http error: 503")})
    da = DataAccess(_reg(sec), {"fundamentals": timedelta(seconds=86400)}, cache=TTLCache(clock=lambda: clock[0].timestamp()),
                    store=_store(tmp_path), now_fn=lambda: clock[0])
    da.quarters("BAD")
    first = len(sec.calls)
    clock[0] = now0 + timedelta(hours=2)  # the 1h back-off has passed
    da.quarters("BAD")
    m = da.store.ingestion(FUNDAMENTALS, "BAD")
    assert len(sec.calls) > first, f"no request was made, but the manifest now says attempts={m.attempts}"


# ============================================================================ N8 / N9 (P2) restatement vintages
def _fact(val, end, start, filed, form="10-Q"):  # noqa: ANN001, ANN202
    d = {"val": val, "end": end, "filed": filed, "form": form}
    if start:
        d["start"] = start
    return d


def test_N8_a_restatement_filed_as_an_amendment_is_a_vintage():
    """EPS 10.0 (10-Q, 2024-05-01) restated to 1.0 by a 10-Q/A on 2024-07-15. 445221f: /A forms are ignored."""
    from marketlens.providers.live.sec_edgar import parse_company_facts

    rev = [_fact(100.0, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(100.0, "2024-06-30", "2024-04-01", "2024-08-01")]
    eps = [_fact(10.0, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(1.0, "2024-03-31", "2024-01-01", "2024-07-15", "10-Q/A")]
    qs = parse_company_facts({"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}, "EarningsPerShareDiluted": {"units": {"USD/shares": eps}}}}}, "T")
    q1 = next(q for q in as_of(qs, date(2024, 9, 1)) if q.period_end == date(2024, 3, 31))
    assert q1.eps_diluted == 1.0, f"restatement not visible after its filing (EPS {q1.eps_diluted})"


def test_N9_derived_q4_uses_the_values_known_at_the_10k_date():
    """Q1 revenue revised 100 → 80 in the Q3 10-Q (2024-11-01); the 10-K FY total 380 already reflects it.
    Expected Q4 = 380 − (80+100+100) = 100 and TTM = 380. 445221f: Q4 = 80, TTM = 360."""
    from marketlens.providers.live.sec_edgar import parse_company_facts

    rev = [_fact(100.0, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(100.0, "2024-06-30", "2024-04-01", "2024-08-01"),
           _fact(100.0, "2024-09-30", "2024-07-01", "2024-11-01"), _fact(80.0, "2024-03-31", "2024-01-01", "2024-11-01"),
           _fact(380.0, "2024-12-31", "2024-01-01", "2025-02-15", "10-K")]
    qs = as_of(parse_company_facts({"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}}}}, "T"), date(2025, 3, 1))
    assert compute_metrics(qs).revenue_ttm == 380.0, [(q.period_end.isoformat(), q.revenue) for q in qs]


# ============================================================================ N10 (P2) relisting rewrites history
def test_N10_relisting_under_a_new_ticker_keeps_the_delisting_in_history(tmp_path):
    st = _store(tmp_path)
    st.sync_universe([_sec("OLDT", 5)], date(2026, 6, 1))
    st.sync_universe([], date(2026, 6, 2))  # delisted
    st.sync_universe([_sec("NEWT", 5)], date(2026, 8, 3))  # same CIK lists again months later
    listed = sorted(s.ticker for s in st.securities(date(2026, 6, 10)))
    assert listed == [], f"a company delisted on 2026-06-02 is listed again in the past universe: {listed}"


# ============================================================================ N11 (P2) item 6 on listing pages
def test_N11_listing_pages_do_not_show_an_unchecked_young_buy_as_actionable():
    """Listings call recommendation_status(fetch_quote=False); the quote cache lives 15 s, so after that no quote
    is available. 445221f: up to 60 minutes after the analysis the BUY is CURRENT/actionable without a price check."""
    from marketlens.domain.freshness import PlanCheck, recommendation_freshness

    as_of_ = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)  # 11:00 New York
    plan = PlanCheck(rec_price=100.0, max_buy=102.0, stop=95.0, target1=115.0, min_rr=2.0, bullish=True)
    f = recommendation_freshness(as_of_, "FRESH", as_of_ + timedelta(minutes=30), plan=plan, quote_price=None, quote_ts=None)
    assert not f.actionable, f.status


# ============================================================================ N13 (P2) live-verify fails open on the store
def test_N13_live_verify_never_verifies_a_provider_from_stored_rows():
    """No API keys, no network (Polygon 'not configured'); bars exist in the local store. 445221f: bars VERIFIED
    with provider='store' — with record=True the readiness matrix drops NOT_LIVE_VERIFIED for daily bars."""
    from marketlens.application import live_verify
    from marketlens.domain.market_calendar import last_completed_session
    from tests.integration.test_service_api import make_service

    svc = make_service(mode=DataMode.LIVE)
    day = last_completed_session(svc.now())
    for t in ("NVDA", "AAPL", "MSFT"):
        svc.store.save_bars(t, [Bar(day - timedelta(days=i), 100, 101, 99, 100, 1e6) for i in range(20)], "polygon")
    rep = live_verify.verify(svc, tickers=("NVDA", "AAPL", "MSFT"), record=False)
    assert rep["categories"]["bars"]["status"] != "VERIFIED", rep["categories"]["bars"]["samples"]


# ============================================================================ N14 (P3) repeated reuse
def test_N14_a_third_reuse_of_one_ticker_does_not_break_the_universe_sync(tmp_path):
    """ZZ: CIK 1 → CIK 2 → CIK 1 → CIK 2. 445221f: the archive key ZZ~1 exists already → IntegrityError on every
    later sync (MarketSync.run catches only ProviderError, so the whole sync stops)."""
    st = _store(tmp_path)
    for i, cik in enumerate((1, 2, 1, 2)):
        st.sync_universe([_sec("ZZ", cik)], date(2026, 6, 1) + timedelta(days=i))
