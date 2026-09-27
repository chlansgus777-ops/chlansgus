"""7차 독립 평가 (대상 커밋 2240992) — 새 반례.

각 테스트는 입력과 기대 동작을 그대로 적었다. 2240992 에서는 모두 실패하고, 고친 뒤에는 통과해야 한다.
실행: 저장소 루트에서  python -m pytest -q -o addopts="" evaluations/eval7
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from marketlens.application.market_store import MarketStore
from marketlens.infrastructure.db.models import Base
from marketlens.infrastructure.db.session import make_engine, make_session_factory

UTC = timezone.utc


def _store(tmp_path) -> MarketStore:
    eng = make_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    Base.metadata.create_all(eng)
    return MarketStore(make_session_factory(eng), "LIVE")


def _fact(val: float, end: str, start: str | None, filed: str, form: str = "10-Q") -> dict:
    d = {"val": val, "end": end, "filed": filed, "form": form}
    if start:
        d["start"] = start
    return d


def _view(facts: dict, ticker: str, day: date) -> list:
    from marketlens.domain.fundamentals import as_of
    from marketlens.providers.live.sec_edgar import parse_company_facts

    return as_of(parse_company_facts(facts, ticker), day)


# ============================================================================ J1 (P1) guidance: the K1 class is still open
# The K1 fix lists change words (reduce, decline, increase, impact, headwind, dilutive, "grow by" …). The same kind of
# sentence written with another verb is still stored as the guided level (a change stored as a positive level).
CHANGE_WITH_OTHER_WORDS = [
    ("eps", "We expect new tariffs to lower fourth quarter EPS by approximately $0.10."),
    ("eps", "We expect tariffs to cost approximately $0.10 per share in the fourth quarter."),
    ("eps", "We expect a $0.10 per share hit from tariffs in the fourth quarter."),
    ("eps", "Tariffs are expected to weigh on fourth quarter EPS by about $0.10."),
    ("eps", "We expect the stronger dollar to shave approximately $0.05 off fourth quarter EPS."),
    ("eps", "We expect the acquisition to add $0.05 to $0.10 to fiscal 2027 EPS."),
    ("eps", "We expect fourth quarter EPS to be down $0.05 to $0.10 year over year."),
    ("revenue", "We expect foreign exchange to be a drag of approximately $150 million on fourth quarter revenue."),
    ("revenue", "We expect the acquisition to contribute approximately $200 million of revenue in the fourth quarter."),
    ("revenue", "We expect fourth quarter revenue to be up $50 million to $60 million sequentially."),
    ("revenue", "We expect fiscal 2027 revenue growth of $2.0 billion to $2.5 billion."),
    ("gross_margin", "We expect tariffs to pressure gross margin by approximately 1.5% in the fourth quarter."),
]
# A per-share amount is read as EPS guidance whatever it is: a charge, an expense, a dividend, a deal price.
PER_SHARE_AMOUNTS_THAT_ARE_NOT_EPS = [
    "We expect restructuring charges of $0.10 to $0.15 per share in the fourth quarter.",
    "We expect stock-based compensation expense of approximately $0.45 per share for fiscal 2027.",
    "We expect amortization of acquired intangibles of approximately $0.35 per share in fiscal 2027.",
    "The Board expects to maintain the quarterly dividend of $0.26 per share in the fourth quarter.",
    "The transaction, valued at $25.00 per share in cash, is expected to close in the fourth quarter of 2026.",
]


@pytest.mark.parametrize("metric,sentence", CHANGE_WITH_OTHER_WORDS)
def test_J1_a_change_written_with_another_verb_is_never_stored_as_the_guided_level(metric, sentence):
    from marketlens.domain.guidance import extract

    items = [i for i in extract(sentence) if i.metric == metric]
    assert all(i.status != "EXTRACTED" for i in items), [(i.low, i.high, i.status, i.period_label) for i in items]


@pytest.mark.parametrize("sentence", PER_SHARE_AMOUNTS_THAT_ARE_NOT_EPS)
def test_J1_a_per_share_amount_that_is_not_earnings_is_never_eps_guidance(sentence):
    from marketlens.domain.guidance import extract

    items = [i for i in extract(sentence) if i.metric == "eps"]
    assert all(i.status != "EXTRACTED" for i in items), [(i.low, i.high, i.status, i.period_label) for i in items]


@pytest.mark.parametrize("text", [
    # an outlook heading with bullets (not extracted: no forward-looking word), then a tariff note in other words
    "Fourth Quarter Fiscal 2026 Outlook\n• Revenue of $10.2 billion to $10.4 billion\n• GAAP diluted EPS of $1.30 to $1.35\n"
    "• Non-GAAP diluted EPS of $1.45 to $1.50\nThe outlook includes an expected $0.10 per share hit from new tariffs.",
    # the revenue level written with its growth (UNCLEAR since the K1 fix, EXTRACTED on f80c460), then the FX note
    "Fourth quarter outlook. For the fourth quarter of fiscal 2026, we expect revenue of $10.2 billion to $10.4 billion, an increase "
    "of 8% to 10% year over year. We expect foreign exchange to be a drag of approximately $150 million on fourth quarter revenue.",
], ids=["eps-hit-after-bullets", "revenue-drag-after-level-with-growth"])
def test_J1_end_to_end_an_in_line_outlook_is_not_read_as_a_guide_far_below_consensus(tmp_path, text):
    """Guidance in line with the pre-release consensus (EPS 1.47, revenue 10.3 billion) and a quarter that beat.
    2240992: the only EXTRACTED item is the tariff / FX note → EPS guidance +0.10 (−93%) or revenue guidance
    150 million (−98.5%) → BEAT_WEAK_GUIDE. On f80c460 the second layout was read correctly (the level came first)."""
    from marketlens.application.estimate_book import attach_guidance
    from marketlens.domain.earnings import EarningsReport, assess_earnings
    from marketlens.domain.estimates import EstimateObservation
    from marketlens.domain.guidance import extract

    st = _store(tmp_path)
    st.save_guidance("ABC", "0000-26-000031", datetime(2026, 11, 5, 21, tzinfo=UTC), "https://www.sec.gov/x", extract(text))
    st.save_estimates([EstimateObservation("ABC", "finnhub", "2027Q1", "quarter", None, date(2026, 11, 1), eps=1.47, revenue=10.3e9,
                                           report_date=date(2027, 2, 5))])
    reports = [EarningsReport(date(2026, 11, 5), "Q3 FY2026", "finnhub", revenue_actual=10.5e9, revenue_consensus=10.2e9, eps_actual=1.52, eps_consensus=1.40)]
    rep = attach_guidance(reports, st.guidance("ABC", datetime(2026, 11, 6, tzinfo=UTC)), st.estimate_history("ABC", date(2026, 11, 6)), date(2026, 11, 6))
    a = assess_earnings(rep)
    gaps = (a.guide_eps_vs_cons, a.guide_rev_vs_cons, a.fy_guide_rev_vs_cons)
    assert all(g is None or abs(g) < 0.5 for g in gaps), (gaps, a.result_quality)


# ============================================================================ J2 (P2) share_basis reads a doubled share count as a split
def test_J2_a_merger_that_doubles_the_share_count_is_not_a_two_for_one_split():
    """No split. A stock-for-stock merger at the start of Q2 lifts the diluted count from 100M to 233M; EPS is 1.00
    in every quarter and the 10-K reports FY EPS 4.00 on 199.75M weighted shares. FY/Q1 shares = 1.9975, within 6%
    of 2, so share_basis halves Q1 EPS before the subtraction: Q4 EPS 1.50 and TTM EPS 4.50 (≠ the reported FY EPS
    4.00). f80c460 derived 1.00 and 4.00 — the error came with the K2 fix."""
    from marketlens.domain.fundamentals import compute_metrics

    ni = [_fact(100e6, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(233e6, "2024-06-30", "2024-04-01", "2024-08-01"),
          _fact(233e6, "2024-09-30", "2024-07-01", "2024-11-01"), _fact(799e6, "2024-12-31", "2024-01-01", "2025-02-15", "10-K")]
    sh = [_fact(100e6, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(233e6, "2024-06-30", "2024-04-01", "2024-08-01"),
          _fact(233e6, "2024-09-30", "2024-07-01", "2024-11-01"), _fact(199.75e6, "2024-12-31", "2024-01-01", "2025-02-15", "10-K")]
    eps = [_fact(1.00, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(1.00, "2024-06-30", "2024-04-01", "2024-08-01"),
           _fact(1.00, "2024-09-30", "2024-07-01", "2024-11-01"), _fact(4.00, "2024-12-31", "2024-01-01", "2025-02-15", "10-K")]
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": ni}}, "EarningsPerShareDiluted": {"units": {"USD/shares": eps}},
                                   "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": sh}}}}}
    view = _view(facts, "MRG", date(2025, 3, 1))
    q4 = next(q for q in view if q.period_end == date(2024, 12, 31))
    assert q4.eps_diluted is None or abs(q4.eps_diluted - 1.00) < 0.05, q4.eps_diluted
    ttm = compute_metrics(view).eps_ttm
    assert ttm is None or abs(ttm - 4.00) < 0.05, ttm


# ============================================================================ J3 (P2) first-reporter concept rule compares two definitions
def test_J3_after_a_concept_switch_year_over_year_growth_uses_one_definition():
    """Revenues (old concept) Q1 2024 = 100. From the Q1 2025 10-Q (2025-05-01) the company tags
    RevenueFromContractWithCustomerExcludingAssessedTax: Q1 2025 = 99 and the comparable Q1 2024 = 90 (the company's
    own growth: +10%). 2240992 keeps Q1 2024 on the old concept (100) and reports −1% as of 2025-06-01.
    f80c460 reported +10% — the error came with the K3 fix (the view as of 2025-03-01 must still show 100)."""
    from marketlens.domain.fundamentals import compute_metrics

    old = [_fact(100.0, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(100.0, "2024-06-30", "2024-04-01", "2024-08-01"),
           _fact(100.0, "2024-09-30", "2024-07-01", "2024-11-01"), _fact(400.0, "2024-12-31", "2024-01-01", "2025-02-15", "10-K")]
    new = [_fact(99.0, "2025-03-31", "2025-01-01", "2025-05-01"), _fact(90.0, "2024-03-31", "2024-01-01", "2025-05-01")]
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": old}},
                                   "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": new}}}}}
    g = compute_metrics(_view(facts, "T", date(2025, 6, 1))).revenue_growth_yoy
    assert g is None or abs(g - 0.10) < 0.01, g


# ============================================================================ J4 (P3) recast_on flags the following year's 10-K too
def test_J4_a_10k_that_repeats_an_earlier_recast_is_not_itself_a_recast():
    """The FY2023 10-K recast FY2022 (380 → 300). The FY2024 10-K recasts nothing: FY2023 and FY2022 appear exactly as
    filed a year before, and 2024's quarters are on the new basis. recast_on compares with the FIRST value (380), so
    the FY2024 10-K counts as a recast and Q4 2024 is left empty (true 345 − 255 = 90): after every recast, TTM and
    P/E are missing for another year's 10-K window."""
    revs = [
        _fact(95.0, "2022-03-31", "2022-01-01", "2022-05-01"), _fact(95.0, "2022-06-30", "2022-04-01", "2022-08-01"),
        _fact(95.0, "2022-09-30", "2022-07-01", "2022-11-01"), _fact(380.0, "2022-12-31", "2022-01-01", "2023-02-15", "10-K"),
        _fact(100.0, "2023-03-31", "2023-01-01", "2023-05-01"), _fact(100.0, "2023-06-30", "2023-04-01", "2023-08-01"),
        _fact(100.0, "2023-09-30", "2023-07-01", "2023-11-01"),
        _fact(320.0, "2023-12-31", "2023-01-01", "2024-02-15", "10-K"), _fact(300.0, "2022-12-31", "2022-01-01", "2024-02-15", "10-K"),
        _fact(85.0, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(85.0, "2024-06-30", "2024-04-01", "2024-08-01"),
        _fact(85.0, "2024-09-30", "2024-07-01", "2024-11-01"),
        _fact(345.0, "2024-12-31", "2024-01-01", "2025-02-15", "10-K"), _fact(320.0, "2023-12-31", "2023-01-01", "2025-02-15", "10-K"),
        _fact(300.0, "2022-12-31", "2022-01-01", "2025-02-15", "10-K"),
    ]
    q4 = next(q for q in _view({"facts": {"us-gaap": {"Revenues": {"units": {"USD": revs}}}}}, "R", date(2025, 3, 1))
              if q.period_end == date(2024, 12, 31))
    assert q4.revenue is not None and abs(q4.revenue - 90.0) < 1, q4.revenue


# ============================================================================ J5 (P2) a recast is not seen when the 10-K also switches concept
def test_J5_a_recast_filed_under_a_new_concept_does_not_mix_bases_in_q4():
    """The K2 (b) case — a segment sold in Q4, the 10-K on the continuing basis (FY 2024 320, FY 2023 380 → 300) —
    with one change: the 10-K tags revenue under RevenueFromContractWithCustomerExcludingAssessedTax, while 2023 and
    the 2024 10-Qs used Revenues. The first-reporter rule keeps FY 2023 on the old concept (380), so the recast is
    never seen and Q4 2024 = 320 − 300 = 20 again (true 80 on the continuing basis, or unknown)."""
    old = [_fact(95.0, "2023-03-31", "2023-01-01", "2023-05-01"), _fact(95.0, "2023-06-30", "2023-04-01", "2023-08-01"),
           _fact(95.0, "2023-09-30", "2023-07-01", "2023-11-01"), _fact(380.0, "2023-12-31", "2023-01-01", "2024-02-15", "10-K"),
           _fact(100.0, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(100.0, "2024-06-30", "2024-04-01", "2024-08-01"),
           _fact(100.0, "2024-09-30", "2024-07-01", "2024-11-01")]
    new = [_fact(320.0, "2024-12-31", "2024-01-01", "2025-02-15", "10-K"), _fact(300.0, "2023-12-31", "2023-01-01", "2025-02-15", "10-K")]
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": old}},
                                   "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": new}}}}}
    q4 = next(q for q in _view(facts, "D", date(2025, 3, 1)) if q.period_end == date(2024, 12, 31))
    assert q4.revenue is None or abs(q4.revenue - 80.0) < 1, q4.revenue


# ============================================================================ J6 (P3) a late 10-K bounds the next quarter's release
def test_J6_a_late_filers_delayed_q4_release_does_not_date_the_next_quarter():
    """A late filer (NT 10-K): the Q4 release and the 10-K come in mid-April, the Q1 release on 05-12. The first
    periodic filing after the Q1 period end is the late 10-K (04-15), so Q1's window ends there and the delayed Q4
    release (04-14) becomes Q1's release: Q1 EPS 0.55 counts as known four weeks before it was published."""
    from marketlens.application.pipeline import earnings_visible
    from marketlens.domain.earnings import pair_with_releases

    rows = [{"period": date(2025, 12, 31), "actual": 0.40, "estimate": 0.42, "quarter": 4, "year": 2025},
            {"period": date(2026, 3, 31), "actual": 0.55, "estimate": 0.50, "quarter": 1, "year": 2026}]
    releases = [datetime(2026, 4, 14, 20, 5, tzinfo=UTC), datetime(2026, 5, 12, 20, 5, tzinfo=UTC)]
    periodic = [datetime(2026, 4, 15, 21, 0, tzinfo=UTC), datetime(2026, 5, 15, 21, 0, tzinfo=UTC)]  # late 10-K, then the Q1 10-Q
    reps = pair_with_releases(rows, releases, "finnhub+sec-8k", periodic)
    before_q1_release = datetime(2026, 4, 20, 21, 0, tzinfo=UTC)
    assert not any(r.eps_actual == 0.55 and earnings_visible(r, before_q1_release) for r in reps), [(r.fiscal_label, r.report_date) for r in reps]
