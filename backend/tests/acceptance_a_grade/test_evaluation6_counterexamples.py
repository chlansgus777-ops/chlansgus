"""6차 독립 평가 (대상 커밋 f80c460) — 새 반례. 원본: evaluations/eval6 (평가자 작성, 수정 없이 옮김).

각 테스트는 입력과 기대 동작을 그대로 적었다. f80c460 에서는 모두 실패하고, 고친 뒤에는 통과해야 한다.
실행: 저장소 루트에서  python -m pytest -q -o addopts="" evaluations/eval6
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

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


def _fact(val: float, end: str, start: str | None, filed: str, form: str = "10-Q") -> dict:
    d = {"val": val, "end": end, "filed": filed, "form": form}
    if start:
        d["start"] = start
    return d


# ============================================================================ K1 (P1) guidance: a change or impact amount stored as the guided level
# The extractor stores any single amount in a forward-looking sentence with one metric as that metric's guidance.
# (a) An amount by which something CHANGES (tariff / FX / acquisition impact, "decline by", "increase by") is not the
#     level of that metric. f80c460 stores it as the level, with a positive sign even when the change is a reduction.
CHANGE_OR_IMPACT_AMOUNT = [
    ("eps", "We expect new tariffs to reduce fourth quarter EPS by approximately $0.10."),
    ("eps", "The outlook includes an expected $0.12 per share impact from new tariffs in the fourth quarter."),
    ("eps", "We expect the acquisition to be dilutive to diluted EPS by $0.05 to $0.10 in fiscal 2027."),
    ("revenue", "We expect foreign currency to negatively impact fourth quarter revenue by approximately $150 million."),
    ("revenue", "We expect fourth quarter revenue to decline by $50 million to $60 million sequentially."),
    ("revenue", "We expect fiscal 2027 revenue to increase by $1.0 billion to $1.2 billion."),
    ("gross_margin", "We expect gross margin in the fourth quarter to decrease 1% to 2% sequentially."),
]
# (b) The loss phrase is negated: the company no longer expects a loss, the per-share figure is a profit.
#     f80c460 stores −0.10..−0.05 (EXTRACTED).
NEGATED_LOSS_PHRASE = [
    "Instead of the net loss per share we previously expected, we now expect $0.05 to $0.10 per share for fiscal 2027.",
    "Contrary to our prior outlook of a net loss per share, we now expect $0.05 to $0.10 per share for fiscal 2027.",
]


@pytest.mark.parametrize("metric,sentence", CHANGE_OR_IMPACT_AMOUNT)
def test_K1_a_change_or_impact_amount_is_never_stored_as_the_guided_level(metric, sentence):
    from marketlens.domain.guidance import extract

    items = [i for i in extract(sentence) if i.metric == metric]
    assert all(i.status != "EXTRACTED" for i in items), [(i.low, i.high, i.status, i.period_label) for i in items]


@pytest.mark.parametrize("sentence", NEGATED_LOSS_PHRASE)
def test_K1_b_a_negated_loss_phrase_does_not_make_a_profit_negative(sentence):
    from marketlens.domain.guidance import extract

    (item,) = extract(sentence)
    assert item.status == "GUIDANCE_UNCLEAR" or (item.low is not None and item.low > 0), (item.low, item.high, item.status)


def test_K1_c_a_parenthesised_negative_percentage_is_not_stored_positive():
    """Accounting notation "(3%)" is minus 3%. f80c460: +0.03 EXTRACTED (operating margin, display only today)."""
    from marketlens.domain.guidance import extract

    (item,) = extract("For fiscal 2027, we expect operating margin of approximately (3%).")
    assert item.status == "GUIDANCE_UNCLEAR" or (item.high is not None and item.high < 0), (item.low, item.high, item.status)


@pytest.mark.parametrize("text", [
    # an outlook heading with bullets (no forward-looking word in the bullets), then the tariff note
    "Fourth Quarter Fiscal 2026 Outlook\n• Revenue of $10.2 billion to $10.4 billion\n• GAAP diluted EPS of $1.30 to $1.35\n"
    "• Non-GAAP diluted EPS of $1.45 to $1.50\nThe outlook includes an expected $0.10 per share impact from new tariffs.",
    # GAAP and non-GAAP ranges in one sentence (UNCLEAR by design), then the tariff note
    "Fourth quarter outlook. For the fourth quarter of fiscal 2026, we expect GAAP diluted EPS of $1.30 to $1.35 and non-GAAP "
    "diluted EPS of $1.45 to $1.50. We expect new tariffs to reduce fourth quarter EPS by approximately $0.10.",
], ids=["bullets", "gaap-and-non-gaap"])
def test_K1_end_to_end_an_in_line_outlook_with_a_tariff_note_is_not_a_weak_guide(tmp_path, text):
    """Non-GAAP guidance 1.45..1.50 vs a pre-release consensus of 1.47 is in line, and the quarter beat.
    f80c460: the only EXTRACTED EPS item is the tariff impact → next-quarter EPS guidance +0.10 →
    guide_eps_vs_cons −93% → BEAT_WEAK_GUIDE."""
    from marketlens.application.estimate_book import attach_guidance
    from marketlens.domain.earnings import EarningsReport, ResultQuality, assess_earnings
    from marketlens.domain.estimates import EstimateObservation
    from marketlens.domain.guidance import extract

    st = _store(tmp_path)
    st.save_guidance("ABC", "0000-26-000021", datetime(2026, 11, 5, 21, tzinfo=UTC), "https://www.sec.gov/x", extract(text))
    st.save_estimates([EstimateObservation("ABC", "finnhub", "2027Q1", "quarter", None, date(2026, 11, 1), eps=1.47, report_date=date(2027, 2, 5))])
    reports = [EarningsReport(date(2026, 11, 5), "Q3 FY2026", "finnhub", revenue_actual=10.5e9, revenue_consensus=10.2e9, eps_actual=1.52, eps_consensus=1.40)]
    rep = attach_guidance(reports, st.guidance("ABC", datetime(2026, 11, 6, tzinfo=UTC)), st.estimate_history("ABC", date(2026, 11, 6)), date(2026, 11, 6))
    a = assess_earnings(rep)
    assert a.result_quality != ResultQuality.BEAT_WEAK_GUIDE, (a.guide_eps_vs_cons, a.result_quality)


# ============================================================================ K2 (P1) Q4 = FY − (Q1+Q2+Q3) across a basis change made by the 10-K
# (a) a stock split inside the fiscal year (per-share values), (b) discontinued operations recast in the 10-K.
def _nvda_fy2025_facts() -> dict:
    """NVIDIA fiscal 2025 as filed (10:1 split executed 2024-06-10). The Q1 10-Q (filed before the split) reports
    EPS 5.98 on the old share basis; Q2, Q3 and the 10-K report on the new basis. The 10-K tags diluted shares for
    the full year only (no Q4 shares), so Q4 EPS comes from FY EPS − (Q1 + Q2 + Q3)."""
    rev = [_fact(26.0e9, "2024-04-28", "2024-01-29", "2024-05-29"), _fact(30.0e9, "2024-07-28", "2024-04-29", "2024-08-28"),
           _fact(35.1e9, "2024-10-27", "2024-07-29", "2024-11-20"), _fact(130.5e9, "2025-01-26", "2024-01-29", "2025-02-26", "10-K")]
    ni = [_fact(14.9e9, "2024-04-28", "2024-01-29", "2024-05-29"), _fact(16.6e9, "2024-07-28", "2024-04-29", "2024-08-28"),
          _fact(19.3e9, "2024-10-27", "2024-07-29", "2024-11-20"), _fact(72.9e9, "2025-01-26", "2024-01-29", "2025-02-26", "10-K")]
    eps = [_fact(5.98, "2024-04-28", "2024-01-29", "2024-05-29"), _fact(0.67, "2024-07-28", "2024-04-29", "2024-08-28"),
           _fact(0.78, "2024-10-27", "2024-07-29", "2024-11-20"), _fact(2.94, "2025-01-26", "2024-01-29", "2025-02-26", "10-K")]
    wds = [_fact(2.50e9, "2024-04-28", "2024-01-29", "2024-05-29"), _fact(24.8e9, "2024-07-28", "2024-04-29", "2024-08-28"),
           _fact(24.8e9, "2024-10-27", "2024-07-29", "2024-11-20"), _fact(24.8e9, "2025-01-26", "2024-01-29", "2025-02-26", "10-K")]
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}, "NetIncomeLoss": {"units": {"USD": ni}},
                                  "EarningsPerShareDiluted": {"units": {"USD/shares": eps}},
                                  "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": wds}}}}}


def _nvda_view_on(day: date):
    from marketlens.domain.corporate_actions import SplitEvent, normalize_quarters
    from marketlens.domain.fundamentals import as_of
    from marketlens.providers.live.sec_edgar import parse_company_facts

    qs = parse_company_facts(_nvda_fy2025_facts(), "NVDA")
    pit, _ = normalize_quarters(as_of(qs, day), [SplitEvent("NVDA", date(2024, 6, 10), 1, 10, "polygon")], day)
    return pit


def test_K2_q4_eps_is_not_computed_across_a_split_inside_the_fiscal_year():
    """NVIDIA reported Q4 FY2025 diluted EPS 0.89. f80c460 derives 2.94 − (5.98 + 0.67 + 0.78) = −4.49 (the Q1 figure
    is on the pre-split basis) and keeps it: the derived quarter is dated at the 10-K, after the split, so the split
    normalisation never touches it."""
    q4 = next(q for q in _nvda_view_on(date(2025, 3, 15)) if q.period_end == date(2025, 1, 26))
    assert q4.eps_diluted is None or abs(q4.eps_diluted - 0.89) < 0.1, q4.eps_diluted


def test_K2_ttm_eps_after_a_split_year_10k_is_not_negative():
    """From the 10-K (2025-02-26) until the next Q1 10-Q, f80c460 reports NVIDIA's TTM diluted EPS as −2.44
    (true ≈ 2.94): P/E and every EPS-based feature of a profitable mega-cap flip sign for about three months."""
    from marketlens.domain.fundamentals import compute_metrics

    ttm = compute_metrics(_nvda_view_on(date(2025, 3, 15))).eps_ttm
    assert ttm is None or abs(ttm - 2.94) < 0.15, ttm


def _discontinued_ops_facts() -> dict:
    """A segment (20 of revenue, 6 of operating income a quarter) is sold in Q4 2024 and presented as discontinued
    operations. The Q1–Q3 10-Qs were filed before that (they include the segment); the 10-K (2025-02-15) reports
    continuing operations only and recasts the prior year (FY 2023: 380 → 300). Quarterly figures are recast only in
    next year's 10-Qs. The 10-K has no quarterly data (not required since 2021)."""
    rev = [_fact(95.0, "2023-03-31", "2023-01-01", "2023-05-01"), _fact(95.0, "2023-06-30", "2023-04-01", "2023-08-01"),
           _fact(95.0, "2023-09-30", "2023-07-01", "2023-11-01"), _fact(380.0, "2023-12-31", "2023-01-01", "2024-02-15", "10-K"),
           _fact(100.0, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(100.0, "2024-06-30", "2024-04-01", "2024-08-01"),
           _fact(100.0, "2024-09-30", "2024-07-01", "2024-11-01"),
           _fact(320.0, "2024-12-31", "2024-01-01", "2025-02-15", "10-K"), _fact(300.0, "2023-12-31", "2023-01-01", "2025-02-15", "10-K")]
    oi = [_fact(20.0, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(20.0, "2024-06-30", "2024-04-01", "2024-08-01"),
          _fact(20.0, "2024-09-30", "2024-07-01", "2024-11-01"), _fact(56.0, "2024-12-31", "2024-01-01", "2025-02-15", "10-K")]
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}, "OperatingIncomeLoss": {"units": {"USD": oi}}}}}


def _disc_ops_view_on(day: date) -> dict:
    from marketlens.domain.fundamentals import as_of
    from marketlens.providers.live.sec_edgar import parse_company_facts

    return {q.period_end: q for q in as_of(parse_company_facts(_discontinued_ops_facts(), "DDD"), day)}


def test_K2_q4_is_not_derived_from_a_recast_year_minus_quarters_on_the_old_basis():
    """Q4 2024 on the continuing basis is 80 (operating income 14). f80c460: 320 − 300 = 20 and 56 − 60 = −4, dated at
    the 10-K — revenue −80% QoQ and a negative operating margin until the next Q1 10-Q brings recast comparatives."""
    q4 = _disc_ops_view_on(date(2025, 3, 1))[date(2024, 12, 31)]
    assert q4.revenue is None or abs(q4.revenue - 80.0) < 1, q4.revenue
    assert q4.operating_income is None or abs(q4.operating_income - 14.0) < 1, q4.operating_income


def test_K2_a_recast_prior_year_does_not_rewrite_its_q4():
    """Q4 2023 was 95 (380 − 3 × 95), public since 2024-02-15. After the 10-K recasts FY 2023 to 300, f80c460
    derives Q4 2023 = 300 − 285 = 15 (the recast year minus quarters that were never recast)."""
    q4_prev = _disc_ops_view_on(date(2025, 3, 1))[date(2023, 12, 31)]
    assert q4_prev.revenue in (None, 95.0, 75.0), q4_prev.revenue


# ============================================================================ K3 (P2) per-period concept choice rewrites the past
def _concept_switch_facts() -> dict:
    """Revenues (old concept) for Q1–Q3 2024 and FY 2024 (10-K 2025-02-15). From the Q1 2025 10-Q (filed 2025-05-01)
    the company tags RevenueFromContractWithCustomerExcludingAssessedTax (new concept, earlier in the preference order),
    including the prior-year comparative for Q1 2024 (90 on the new definition)."""
    old = [_fact(100.0, "2024-03-31", "2024-01-01", "2024-05-01"), _fact(100.0, "2024-06-30", "2024-04-01", "2024-08-01"),
           _fact(100.0, "2024-09-30", "2024-07-01", "2024-11-01"), _fact(400.0, "2024-12-31", "2024-01-01", "2025-02-15", "10-K")]
    new = [_fact(110.0, "2025-03-31", "2025-01-01", "2025-05-01"), _fact(90.0, "2024-03-31", "2024-01-01", "2025-05-01")]
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": old}},
                                  "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": new}}}}}


def _revenue_on(day: date) -> dict[date, float | None]:
    from marketlens.domain.fundamentals import as_of
    from marketlens.providers.live.sec_edgar import parse_company_facts

    return {q.period_end: q.revenue for q in as_of(parse_company_facts(_concept_switch_facts(), "T"), day)}


def test_K3_a_later_filing_does_not_remove_a_quarter_from_a_past_view():
    """Q1 2024 revenue (100) was public from 2024-05-01. f80c460: in the view as of 2025-03-01 the quarter is gone,
    because the per-period choice picked the new concept for Q1 2024 — whose only fact was filed on 2025-05-01."""
    assert _revenue_on(date(2025, 3, 1)).get(date(2024, 3, 31)) == 100.0, _revenue_on(date(2025, 3, 1))


def test_K3_a_q4_dated_at_the_10k_does_not_use_a_value_filed_after_it():
    """Q4 2024 is derived at the 10-K (2025-02-15): 400 − (100 + 100 + 100) = 100. f80c460 gives 110 as of 2025-03-01,
    computed with the Q1 comparative filed on 2025-05-01 (known_at falls back to a value filed after the 10-K)."""
    assert _revenue_on(date(2025, 3, 1)).get(date(2024, 12, 31)) in (None, 100.0), _revenue_on(date(2025, 3, 1))


# ============================================================================ K4 (P2) a pre-announcement dates the earnings report
def test_K4_a_pre_announcement_under_item_2_02_does_not_date_the_report():
    """Q4 (period end 2025-12-31): a preliminary update furnished under Item 2.02 on 2026-01-12 (e.g. preliminary
    revenue at a January conference), the full release with EPS on 2026-02-05. earnings_release_times returns both
    (every Item 2.02 8-K). f80c460 dates the report 2026-01-12, so the EPS 1.20 counts as known 3.5 weeks before it
    was published, and the guidance filed with the real release (2026-02-05) no longer attaches (±3 days)."""
    from marketlens.application.pipeline import earnings_visible
    from marketlens.domain.earnings import pair_with_releases

    rows = [{"period": date(2025, 12, 31), "actual": 1.20, "estimate": 1.10, "quarter": 4, "year": 2025}]
    times = [datetime(2026, 1, 12, 13, 0, tzinfo=UTC), datetime(2026, 2, 5, 21, 5, tzinfo=UTC)]
    reps = pair_with_releases(rows, times, "finnhub+sec-8k")
    before_release = datetime(2026, 1, 20, 21, 0, tzinfo=UTC)
    assert not any(earnings_visible(r, before_release) for r in reps), [(r.report_date, r.eps_actual) for r in reps]


# ============================================================================ K5 (P3) the relist evidence is judged before the backfill has loaded it
class _Grouped:
    name = "polygon"
    configured = True

    def get_grouped_daily(self, d: date) -> dict[str, Bar]:
        return {"TTT": Bar(d, 50, 51, 49, 50, 1e6)}


class _Chain:
    def __init__(self, providers: list, listing: list[Security] | None = None) -> None:
        self.providers, self._listing = providers, listing

    def call(self, method: str, key: object, *a: object) -> SimpleNamespace:
        return SimpleNamespace(value=self._listing)


class _Registry:
    def __init__(self, listing: list[Security]) -> None:
        self._chains = {"price": _Chain([_Grouped()]), "universe": _Chain([], listing)}

    def chain(self, kind: str) -> _Chain:
        return self._chains.get(kind, _Chain([]))


def test_K5_a_sync_after_a_long_break_does_not_cut_a_stock_that_kept_trading(tmp_path):
    """TTT traded every session. The SEC file omitted it on 2026-06-02, the day a sync ran; the next sync runs
    after a nine-week break. That sync loads only the newest 30 missing days (max_bar_calls) and then judges the
    absence: the first three weeks of it have no bars yet, so f80c460 treats the stock as relisted — its history
    moves to an archive key and the absence is recorded as a delisting. The older days arrive one sync later."""
    from marketlens.application.sync import MarketSync

    st = _store(tmp_path)
    d1, d2 = date(2026, 6, 1), date(2026, 6, 2)
    for d in _sessions(d1 - timedelta(days=120), d2):
        st.save_grouped(d, {"TTT": Bar(d, 50, 51, 49, 50, 1e6)}, "polygon")
    st.sync_universe([_sec("TTT", 3)], d1)
    st.sync_universe([], d2)  # the SEC file omitted TTT that day
    MarketSync(_Registry([_sec("TTT", 3)]), st).run(datetime(2026, 8, 5, 22, 0, tzinfo=UTC))  # nine weeks later
    assert len(st.bars("TTT", d1 - timedelta(days=150), date(2026, 8, 5))) >= 100, "the history of a stock that never stopped trading was cut"
    with st.sf() as s:
        assert repo.delisted_on(s, st.resolve("TTT", d2), "LIVE") is None, "a stock that kept trading is recorded as delisted"
