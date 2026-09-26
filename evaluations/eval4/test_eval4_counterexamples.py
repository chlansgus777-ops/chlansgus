"""4차 독립 평가 (대상 커밋 4182468) — 새 반례.

각 테스트는 입력과 기대 동작을 그대로 적었다. 4182468 에서는 모두 실패하고, 고친 뒤에는 통과해야 한다.
실행: 저장소 루트에서  python -m pytest -q -o addopts="" evaluations/eval4
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from marketlens.application.market_store import MarketStore
from marketlens.domain.enums import Exchange
from marketlens.domain.market import Bar, Security
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


def _bars(st: MarketStore, t: str, start: date, n: int, px: float) -> None:
    st.save_bars(t, [Bar(start + timedelta(days=i), px, px + 1, px - 1, px, 1e6) for i in range(n)], "polygon")


# ============================================================================ M1 (P0) guidance sign, again
# Loss guidance where a period / basis / verb sits between the loss phrase and the number. 4182468 requires the
# loss phrase to end right before the number, so every one of these is stored POSITIVE (EXTRACTED).
LOSS_WITH_WORDS_BETWEEN = [
    "Net loss per share for fiscal 2027 is expected to be in the range of $1.10 to $1.20.",
    "For fiscal 2027, GAAP net loss per share for the full year is expected to be $1.10 to $1.20.",
    "We expect a net loss for the fourth quarter of fiscal 2026 of $1.10 to $1.20 per share.",
    "For the fourth quarter of fiscal 2026, we expect a loss per diluted share, on a GAAP basis, of $1.10 to $1.20.",
    "For the fourth quarter of fiscal 2026, we expect GAAP loss per share to range from $1.10 to $1.20.",
    "For fiscal 2027, loss per share is now expected to be $1.10 to $1.20.",
    "For fiscal 2027, we expect loss per share to widen to $1.10 to $1.20.",
]


@pytest.mark.parametrize("sentence", LOSS_WITH_WORDS_BETWEEN)
def test_M1_loss_guidance_is_never_stored_as_a_profit(sentence):
    from marketlens.domain.guidance import extract

    (item,) = extract(sentence)
    assert item.status == "GUIDANCE_UNCLEAR" or (item.low is not None and item.high is not None and item.high < 0), (item.low, item.high, item.status)


def test_M1_an_excluded_loss_is_not_taken_as_the_guided_eps():
    """The guided EPS is +2.40..+2.50; the loss range belongs to the excluded divestiture.
    4182468: stored −0.15..−0.10 (the excluded amount, negated) as the EPS guidance."""
    from marketlens.domain.guidance import extract

    (item,) = extract("Excluding the expected loss of $0.10 to $0.15 per share on the divestiture, we expect diluted EPS of $2.40 to $2.50 for fiscal 2027.")
    assert item.status == "GUIDANCE_UNCLEAR" or (item.low, item.high) == (2.40, 2.50), (item.low, item.high, item.status)


def test_M1_gaap_loss_and_non_gaap_eps_in_one_sentence_is_not_resolved_by_taking_the_first_range():
    """Two EPS measures in one sentence. 4182468 takes the first range (GAAP loss −0.40..−0.30), which is then
    compared with the (usually non-GAAP) consensus."""
    from marketlens.domain.guidance import extract

    (item,) = extract("For fiscal 2027, we expect a GAAP net loss of $0.30 to $0.40 per share and non-GAAP EPS of $0.10 to $0.15.")
    assert item.status == "GUIDANCE_UNCLEAR", (item.low, item.high, item.status)


def test_M1_end_to_end_in_line_loss_guidance_is_not_a_guide_up(tmp_path):
    """Loss guidance −1.20..−1.10 vs a pre-release loss consensus of −1.15 is in line.
    4182468: stored +1.10..+1.20 → guide_eps_vs_cons +200% → GUIDE_UP (the evaluation 2 P0 direction)."""
    from marketlens.application.estimate_book import attach_guidance
    from marketlens.domain.earnings import EarningsReport, ResultQuality, assess_earnings
    from marketlens.domain.estimates import EstimateObservation
    from marketlens.domain.guidance import extract

    st = _store(tmp_path)
    text = "Fourth quarter outlook. Net loss per share for the fourth quarter of fiscal 2026 is expected to be $1.10 to $1.20."
    st.save_guidance("ABC", "0000-26-000009", datetime(2026, 11, 5, 21, tzinfo=UTC), "https://www.sec.gov/x", extract(text))
    st.save_estimates([EstimateObservation("ABC", "finnhub", "2027Q1", "quarter", None, date(2026, 11, 1), eps=-1.15, report_date=date(2027, 2, 5))])
    reports = [EarningsReport(date(2026, 11, 5), "Q3 FY2026", "finnhub", revenue_actual=1.0e9, revenue_consensus=1.0e9, eps_actual=-1.00, eps_consensus=-1.00)]
    rep = attach_guidance(reports, st.guidance("ABC", datetime(2026, 11, 6, tzinfo=UTC)), st.estimate_history("ABC", date(2026, 11, 6)), date(2026, 11, 6))
    a = assess_earnings(rep)
    assert a.result_quality != ResultQuality.GUIDE_UP, (a.guide_eps_vs_cons, a.result_quality)


# ============================================================================ M2 (P2) same-ticker relist
def test_M2_a_relist_under_the_same_ticker_keeps_the_delisting_in_history(tmp_path):
    """OLDT (CIK 5) delisted 2026-06-02, lists again as OLDT on 2026-08-03 (e.g. back from OTC).
    4182468: the update branch clears delisted_at → delisted_on None and OLDT is 'listed' on 2026-06-10."""
    st = _store(tmp_path)
    st.sync_universe([_sec("OLDT", 5)], date(2026, 6, 1))
    st.sync_universe([], date(2026, 6, 2))
    st.sync_universe([_sec("OLDT", 5)], date(2026, 8, 3))
    listed = sorted(s.ticker for s in st.securities(date(2026, 6, 10)))
    assert listed == [], f"the past universe lists a delisted company during its delisting: {listed}"
    with st.sf() as s:
        assert repo.delisted_on(s, "OLDT", "LIVE") is not None or listed == [], "the delisting was erased"


# ============================================================================ M3 (P2) rename back
def test_M3_a_rename_back_to_the_old_ticker_is_a_rename_not_a_delisting(tmp_path):
    """AAA → BBB (2026-06-10) → AAA (2026-08-03), same CIK throughout.
    4182468: BBB is recorded DELISTED on 2026-08-03 and bars('AAA') loses the BBB period (15 of 25 bars)."""
    st = _store(tmp_path)
    d1, d2, d3 = date(2026, 6, 1), date(2026, 6, 10), date(2026, 8, 3)
    st.sync_universe([_sec("AAA", 7)], d1)
    _bars(st, "AAA", d1 - timedelta(days=10), 10, 50.0)
    st.sync_universe([_sec("BBB", 7)], d2)
    _bars(st, "BBB", d2, 10, 51.0)
    st.sync_universe([_sec("AAA", 7)], d3)
    _bars(st, "AAA", d3, 5, 52.0)
    with st.sf() as s:
        assert repo.delisted_on(s, "BBB", "LIVE") is None, "a rename back was recorded as BBB's delisting"
    assert len(st.bars("AAA", d1 - timedelta(days=20), d3 + timedelta(days=10))) == 25, "the BBB period is missing from the company's history"


# ============================================================================ M4 (P2) share classes renamed together
def test_M4_share_classes_renamed_in_one_sync_keep_their_own_histories(tmp_path):
    """One CIK lists class A (XA, ~600) and class B (XB, ~400); both are renamed in one sync (YA, YB).
    4182468 links each new ticker to 'the most recently updated' unlisted row of the CIK. With the SEC file in a
    stable order (A before B every day) YA receives class B's history and YB class A's; with the rename day's
    order reversed it happens to pair correctly — the result depends on the listing order."""
    d1 = date(2026, 6, 1)
    for reverse_today in (False, True):
        st = _store(tmp_path / str(reverse_today))
        for k in range(3):
            st.sync_universe([_sec("XA", 9), _sec("XB", 9)], d1 + timedelta(days=k))
        _bars(st, "XA", d1 - timedelta(days=10), 10, 600.0)
        _bars(st, "XB", d1 - timedelta(days=10), 10, 400.0)
        today = [_sec("YA", 9), _sec("YB", 9)]
        st.sync_universe(today[::-1] if reverse_today else today, d1 + timedelta(days=5))
        ya = {b.close for b in st.bars("YA", d1 - timedelta(days=20), d1 + timedelta(days=5))}
        yb = {b.close for b in st.bars("YB", d1 - timedelta(days=20), d1 + timedelta(days=5))}
        # acceptable: the right class history, or no link when the pairing cannot be decided — never the other class
        assert 400.0 not in ya and 600.0 not in yb, f"order {'YB,YA' if reverse_today else 'YA,YB'}: class histories swapped: YA {sorted(ya)}  YB {sorted(yb)}"
