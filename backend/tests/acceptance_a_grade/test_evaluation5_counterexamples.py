"""5차 독립 평가 (대상 커밋 f6b048b) — 새 반례. 원본: evaluations/eval5 (평가자 작성, 수정 없이 옮김).

각 테스트는 입력과 기대 동작을 그대로 적었다. f6b048b 에서는 모두 실패하고, 고친 뒤에는 통과해야 한다.
실행: 저장소 루트에서  python -m pytest -q -o addopts="" evaluations/eval5
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from marketlens.application.market_store import MarketStore
from marketlens.domain.enums import Exchange
from marketlens.domain.market import Bar, Security
from marketlens.domain.market_calendar import is_trading_day
from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import Base
from marketlens.infrastructure.db.session import make_engine, make_session_factory

UTC = timezone.utc


def _store(tmp_path) -> MarketStore:
    eng = make_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    Base.metadata.create_all(eng)
    return MarketStore(make_session_factory(eng), "LIVE")


def _sec(t: str, cik: int) -> Security:
    return Security(t, t, Exchange.NASDAQ, "Unknown", "Unknown", None, cik=cik)


def _sessions(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


# ============================================================================ L1 (P1) guidance sign, wrong sign still EXTRACTED
# The applicant asked for sentences that come out EXTRACTED with the WRONG sign (UNCLEAR is intended behaviour).
# (a) "net loss" appears before a POSITIVE EPS figure that another EPS phrase governs → stored negative.
PROFIT_AFTER_A_LOSS_PHRASE = [
    "For the fourth quarter, we expect a GAAP net loss and non-GAAP EPS of $0.10 to $0.15.",
    "While we expect a GAAP net loss for fiscal 2027, we expect adjusted EPS of $0.40 to $0.50.",
    "After a net loss in fiscal 2026, the Company expects diluted EPS of $1.10 to $1.20 in fiscal 2027.",
    "We expect to narrow the net loss and deliver diluted EPS of $0.05 to $0.10 in the fourth quarter.",
]
# (b) a loss written without a loss word, or with an en dash (U+2013) as the minus sign → stored positive.
LOSS_WITHOUT_A_LOSS_WORD = [
    "The Company expects to lose between $0.10 and $0.12 per share in the fourth quarter.",
    "For the fourth quarter, we expect diluted EPS of approximately –$0.10.",
    "For fiscal 2027, we expect operating margin of approximately –5%.",
]


@pytest.mark.parametrize("sentence", PROFIT_AFTER_A_LOSS_PHRASE)
def test_L1_a_profit_figure_after_a_loss_phrase_is_never_stored_negative(sentence):
    from marketlens.domain.guidance import extract

    (item,) = extract(sentence)
    assert item.status == "GUIDANCE_UNCLEAR" or (item.low is not None and item.low > 0), (item.low, item.high, item.status)


@pytest.mark.parametrize("sentence", LOSS_WITHOUT_A_LOSS_WORD)
def test_L1_a_loss_without_a_loss_word_is_never_stored_positive(sentence):
    from marketlens.domain.guidance import extract

    (item,) = extract(sentence)
    assert item.status == "GUIDANCE_UNCLEAR" or (item.high is not None and item.high < 0), (item.low, item.high, item.status)


def test_L1_end_to_end_in_line_non_gaap_guidance_is_not_a_weak_guide(tmp_path):
    """Non-GAAP guidance +0.10..+0.15 vs a pre-release consensus of +0.12 is in line.
    f6b048b: stored −0.15..−0.10 → guide_eps_vs_cons −204% → BEAT_WEAK_GUIDE."""
    from marketlens.application.estimate_book import attach_guidance
    from marketlens.domain.earnings import EarningsReport, ResultQuality, assess_earnings
    from marketlens.domain.estimates import EstimateObservation
    from marketlens.domain.guidance import extract

    st = _store(tmp_path)
    text = "Fourth quarter outlook. For the fourth quarter of fiscal 2026, we expect a GAAP net loss and non-GAAP EPS of $0.10 to $0.15."
    st.save_guidance("ABC", "0000-26-000011", datetime(2026, 11, 5, 21, tzinfo=UTC), "https://www.sec.gov/x", extract(text))
    st.save_estimates([EstimateObservation("ABC", "finnhub", "2027Q1", "quarter", None, date(2026, 11, 1), eps=0.12, report_date=date(2027, 2, 5))])
    reports = [EarningsReport(date(2026, 11, 5), "Q3 FY2026", "finnhub", revenue_actual=1.02e9, revenue_consensus=1.0e9, eps_actual=0.11, eps_consensus=0.10)]
    rep = attach_guidance(reports, st.guidance("ABC", datetime(2026, 11, 6, tzinfo=UTC)), st.estimate_history("ABC", date(2026, 11, 6)), date(2026, 11, 6))
    a = assess_earnings(rep)
    assert a.result_quality != ResultQuality.BEAT_WEAK_GUIDE, (a.guide_eps_vs_cons, a.result_quality)


# ============================================================================ L2 (P2) the 7-day rule counts sync days, not absence
def test_L2_one_observed_absence_and_a_long_sync_gap_is_not_a_relist(tmp_path):
    """TTT traded every session (grouped-daily bars are stored throughout). The SEC file omitted it on the one day a
    sync ran (2026-06-02); the next sync ran 10 days later. f6b048b treats this as a relist: all bars move to the
    archive TTT~3 (bars('TTT') = 0, so the scanner drops it for ~60 sessions) and a recommendation made on 06-01
    resolves to an archive delisted on 06-02 (DELISTED_LAST_PRICE outcomes)."""
    st = _store(tmp_path)
    d1 = date(2026, 6, 1)
    st.sync_universe([_sec("TTT", 3)], d1)
    st.save_bars("TTT", [Bar(d, 50, 51, 49, 50, 1e6) for d in _sessions(d1 - timedelta(days=120), d1 + timedelta(days=12))], "polygon")
    st.sync_universe([], d1 + timedelta(days=1))
    st.sync_universe([_sec("TTT", 3)], d1 + timedelta(days=11))
    assert len(st.bars("TTT", d1 - timedelta(days=150), d1 + timedelta(days=12))) >= 80, "the history of a stock that never stopped trading was cut"
    with st.sf() as s:
        assert repo.delisted_on(s, st.resolve("TTT", d1), "LIVE") is None, "a stock that kept trading is recorded as delisted"


# ============================================================================ L3 (P2) an undecidable pairing is recorded as a delisting
def test_L3_a_rename_the_store_cannot_pair_is_not_recorded_as_a_delisting(tmp_path):
    """CIK 5 once had two classes; OLDA was retired. Months later the remaining class XYZA is renamed NEWA — one
    old ticker listed until yesterday, one new ticker today. Because the retired OLDA also ends in 'A', f6b048b
    cannot pair them, leaves NEWA unlinked (no history) and records XYZA as DELISTED on the rename day."""
    st = _store(tmp_path)
    d1 = date(2026, 6, 1)
    st.sync_universe([_sec("OLDA", 5), _sec("XYZA", 5)], d1)
    st.sync_universe([_sec("XYZA", 5)], d1 + timedelta(days=1))  # OLDA retired
    st.save_bars("XYZA", [Bar(d1 - timedelta(days=i + 1), 30, 30, 30, 30, 1e6) for i in range(10)], "polygon")
    st.sync_universe([_sec("NEWA", 5)], d1 + timedelta(days=40))  # XYZA → NEWA
    with st.sf() as s:
        xyz = repo.delisted_on(s, "XYZA", "LIVE")
    # acceptable: linked as a rename, or left unresolved — never a delisting that outcomes will treat as final
    assert xyz is None, f"an unpaired rename was recorded as XYZA's delisting on {xyz}"
