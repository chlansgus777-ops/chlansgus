"""Counterexamples reproduced by the second independent evaluation — each one must stay fixed."""

from __future__ import annotations

from datetime import date, datetime, timezone
from types import SimpleNamespace

from marketlens.domain.corporate_actions import SplitEvent
from marketlens.domain.market import Bar
from marketlens.domain.paper import simulate

UTC = timezone.utc


def test_paper_plan_levels_follow_a_split_executed_after_the_recommendation():
    """Signal at 100 (stop 95, max buy 102) before a 10:1 split; bars are stored split-adjusted (~10).
    Before the fix the adjusted open 10.0 was compared with the pre-split stop 95 → entry cancelled."""
    from marketlens.application.evaluation_service import EvaluationService

    split = SplitEvent("ABC", date(2026, 9, 28), 1, 10, "polygon")
    svc = SimpleNamespace(data=SimpleNamespace(splits=lambda t: [split]))
    pos = SimpleNamespace(ticker="ABC", recommended_at=datetime(2026, 9, 25, 20, tzinfo=UTC), action="BUY", score=0.7, confidence=0.6,
                          stop=95.0, target1=110.0, target2=120.0, thesis="t", model_version="m", regime="r", sector="s", max_buy=102.0)
    ev = EvaluationService(svc)
    sig = ev._signal(pos, None, date(2026, 9, 30))
    assert (sig.stop, sig.target1, sig.target2, sig.max_buy) == (9.5, 11.0, 12.0, 10.2)
    bars = [Bar(date(2026, 9, 28), 10.0, 10.2, 9.9, 10.1, 1e7), Bar(date(2026, 9, 29), 10.1, 10.3, 10.0, 10.2, 1e7)]
    res = simulate(sig, bars)
    assert res.entry is not None and abs(res.entry.price - 10.007) < 1e-3 and res.open
    # a split not yet applied to the bars (basis date before it) leaves the levels alone
    assert ev._signal(pos, None, date(2026, 9, 27)).stop == 95.0


# ------------------------------------------------------------------ guidance sign (P0)
def _g(text: str):
    from marketlens.domain.guidance import extract

    return [(i.metric, i.low, i.high, i.status) for i in extract(text)]


def test_loss_guidance_is_negative_not_a_profit():
    """Evaluation 2 P0: 'loss per diluted share of $1.10 to $1.20' was stored as +1.10..+1.20 (→ GUIDE_UP)."""
    assert _g("For the third quarter of fiscal 2027, we expect a loss per diluted share of $1.10 to $1.20.") == [("eps", -1.2, -1.1, "EXTRACTED")]
    assert _g("For the full year 2027, we expect net loss of $0.30 to $0.40 per diluted share.") == [("eps", -0.4, -0.3, "EXTRACTED")]


def test_sign_ambiguous_guidance_is_unclear_not_guessed():
    for text in ("For the full year 2027, we expect EPS of -$0.10 to $0.05.",
                 "For the full year 2027, we expect EPS of $(0.10) to $(0.05).",
                 "For the full year 2027, we expect net loss per share between a loss of $0.05 and earnings of $0.02.",
                 "For the full year 2027, we expect operating margin of negative 5% to negative 3%.",
                 "For the full year 2027, we expect revenue of $3.2 billion despite losses from the divestiture."):
        (metric, lo, hi, status), = _g(text)
        assert (lo, hi, status) == (None, None, "GUIDANCE_UNCLEAR"), text


def test_ranges_with_dashes_and_plus_minus_are_not_mistaken_for_negatives():
    assert _g("For the full year 2027, we expect revenue of $3.2 billion - $3.4 billion.") == [("revenue", 3.2e9, 3.4e9, "EXTRACTED")]
    assert _g("For the full year 2027, we expect diluted EPS of $1.10 - $1.20.") == [("eps", 1.1, 1.2, "EXTRACTED")]
    (m, lo, hi, st), = _g("Revenue for the first quarter of fiscal 2027 is expected to be $54.0 billion, plus or minus 2%.")
    assert st == "EXTRACTED" and abs(lo - 52.92e9) < 1 and abs(hi - 55.08e9) < 1


def test_loss_guidance_below_a_loss_consensus_is_guide_down():
    from marketlens.domain.earnings import _surprise

    assert _surprise((-1.2 + -1.1) / 2, -1.0) < 0  # a bigger loss than expected is a miss, never an upgrade


# ------------------------------------------------------------------ freshness: young ≠ valid
def test_young_recommendation_is_checked_against_a_fresh_quote():
    """Evaluation 2: 10 minutes after a BUY (stop 95) the quote is 90 → it was still CURRENT / actionable."""
    from datetime import timedelta

    from marketlens.domain.freshness import PlanCheck, recommendation_freshness

    as_of = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)  # 11:00 New York
    now = as_of + timedelta(minutes=10)
    plan = PlanCheck(rec_price=100.0, max_buy=102.0, stop=95.0, target1=115.0, min_rr=2.0, bullish=True)
    f = recommendation_freshness(as_of, "FRESH", now, plan=plan, quote_price=90.0, quote_ts=now - timedelta(minutes=1))
    assert f.status == "PLAN_INVALIDATED" and not f.actionable and any("손절" in p for p in f.problems)
    # a quote that still fits the plan leaves the young recommendation CURRENT
    ok = recommendation_freshness(as_of, "FRESH", now, plan=plan, quote_price=100.2, quote_ts=now - timedelta(minutes=1))
    assert ok.status == "CURRENT"
    # the quote the analysis itself used (not newer than as_of) is not a re-check
    same = recommendation_freshness(as_of, "FRESH", now, plan=plan, quote_price=90.0, quote_ts=as_of - timedelta(minutes=1))
    assert same.status == "CURRENT"


# ------------------------------------------------------------------ live-verify fails closed
def test_live_verify_rejects_empty_values_and_error_strings():
    """Evaluation 2: value=None and 'value': '실패: SEC 403' were both counted as VERIFIED."""
    from marketlens.application.live_verify import sample_problem

    now = datetime(2026, 9, 25, 15, tzinfo=UTC)
    base = {"ticker": "NVDA", "provider": "p", "timestamp": "2026-09-25T14:00:00+00:00", "source": "s"}
    assert sample_problem({**base, "value": None}, now)
    assert sample_problem({**base, "value": "실패: SEC 403"}, now)
    assert sample_problem({**base, "value": ""}, now)
    assert sample_problem({**base, "value": []}, now)
    assert sample_problem({**base, "value": float("nan")}, now)
    assert sample_problem({**base, "value": 0, "positive": True}, now)
    assert sample_problem({**base, "value": 1.0, "timestamp": "not-a-date"}, now)
    assert sample_problem({**base, "value": 1.0, "timestamp": "2027-01-01"}, now)
    assert sample_problem({**base, "value": 1.0, "provider": ""}, now)
    assert sample_problem({**base, "value": 181.2, "positive": True}, now) is None
    assert sample_problem({**base, "value": -0.4, "timestamp": "2026-08-27"}, now) is None  # a loss is a real value


def test_live_verify_guidance_failure_is_not_verified(monkeypatch):
    from marketlens.application import live_verify

    from marketlens.providers.contracts import ProviderUnavailable

    class SEC403:  # the SEC answers 403 (live-verify now calls the provider itself, never "checked today")
        name, configured = "sec-edgar", True

        def earnings_releases(self, *a):  # noqa: ANN002
            raise ProviderUnavailable("http error: 403 Forbidden")

    class Data:
        reg = SimpleNamespace(chain=lambda kind: SimpleNamespace(providers=[SEC403()]))

        def prefetch_guidance(self, tickers, day):  # noqa: ANN001
            return {"NVDA": "실패: SEC 403"}

        def __getattr__(self, name):  # every other category fails loudly
            def boom(*a, **k):  # noqa: ANN002, ANN003
                raise RuntimeError("not in this test")
            return boom

    svc = SimpleNamespace(now=lambda: datetime(2026, 9, 25, 15, tzinfo=UTC), data=Data(), store=None,
                          base_cfg=SimpleNamespace(scanner=SimpleNamespace(estimate_daily_budget=22)))
    rep = live_verify.verify(svc, tickers=("NVDA",), record=False)
    assert rep["summary"]["guidance"] == "BLOCKED_BY_NETWORK"
    assert "VERIFIED" not in rep["summary"].values()


# ------------------------------------------------------------------ AI claims: sign and exact metric
def _claim_ev(**kv):
    from marketlens.application.evidence import EvidenceBuilder

    eb = EvidenceBuilder("NVDA", datetime(2026, 9, 25, 20, tzinfo=UTC))
    for k, v in kv.items():
        eb.add(k.replace("__", "."), "x", k, v, "sec")
    return eb.items()


def test_unsigned_number_cannot_support_a_negative_value():
    """Evaluation 2: evidence EPS = -5 and 'NVDA EPS is $5.' was accepted."""
    from marketlens.application.committee.claims import unverified

    ev = _claim_ev(fund__eps_ttm=-5.0)
    assert unverified("NVDA EPS is $5.", ev, "NVDA", ["NVDA"])
    assert not unverified("NVDA EPS is -$5.", ev, "NVDA", ["NVDA"])
    assert not unverified("NVDA posted a loss of $5 per share (EPS).", ev, "NVDA", ["NVDA"])
    assert not unverified("NVDA EPS는 5달러 적자입니다.", ev, "NVDA", ["NVDA"])
    pos = _claim_ev(fund__eps_ttm=5.0)
    assert unverified("NVDA EPS was a loss of $5.", pos, "NVDA", ["NVDA"])
    assert not unverified("NVDA EPS is $5.", pos, "NVDA", ["NVDA"])


def test_gross_margin_claim_needs_gross_margin_evidence():
    """Evaluation 2: evidence operating margin 25% supported 'gross margin is 25%'."""
    from marketlens.application.committee.claims import unverified

    ev = _claim_ev(fund__operating_margin=0.25)
    assert unverified("NVDA gross margin is 25%.", ev, "NVDA", ["NVDA"])
    assert unverified("NVDA 매출총이익률은 25%입니다.", ev, "NVDA", ["NVDA"])
    assert not unverified("NVDA operating margin is 25%.", ev, "NVDA", ["NVDA"])
    assert not unverified("NVDA 영업이익률은 25%입니다.", ev, "NVDA", ["NVDA"])
    assert not unverified("NVDA margin is 25%.", ev, "NVDA", ["NVDA"])  # a generic "margin" may be any margin


# ------------------------------------------------------------------ severe consensus conflict
def test_severe_consensus_conflict_excludes_eps_and_blocks_buying(tmp_path):
    """Evaluation 2: Alpha Vantage 1.00 vs Finnhub 0.50 for the same quarter (50% apart) still produced
    BUY with no veto. The conflicting EPS must be excluded and new buying blocked."""
    from marketlens.application.data_access import DataAccess
    from marketlens.domain.decision import decide
    from marketlens.domain.enums import Action, HardVeto
    from marketlens.domain.estimates import EstimateObservation
    from tests.acceptance_a_grade.test_research_integrity import _store
    from tests.helpers import card, ctx, plan

    st = _store(tmp_path)
    day = date(2026, 9, 25)
    st.save_estimates([
        EstimateObservation("ABC", "alphavantage", "2026-10-31", "quarter", date(2026, 10, 31), day, eps=1.00),
        EstimateObservation("ABC", "alphavantage", "2027-01-31", "annual", date(2027, 1, 31), day, eps=4.00),
        EstimateObservation("ABC", "finnhub", "2026Q4", "quarter", None, day, eps=0.50, report_date=date(2026, 11, 20)),
    ])
    f = DataAccess.estimates(SimpleNamespace(store=st), "ABC", day)
    assert f.conflicts and "SEVERE_DATA_CONFLICT" in f.conflicts[0]
    assert f.value.forward_eps is None and f.value.eps_revision_30d is None  # excluded, never averaged / picked
    assert "제외" in f.value.cross_check

    conflict = f.conflicts[0].split("SEVERE_DATA_CONFLICT", 1)[1].strip()
    strong = card(90)
    assert decide(strong, plan(), ctx()).action == Action.BUY  # the same inputs without the conflict
    d = decide(strong, plan(), ctx(estimate_conflict=conflict))
    assert d.action == Action.WAIT and HardVeto.SEVERE_ESTIMATE_CONFLICT in d.vetoes
    held = decide(strong, plan(), ctx(held=True, estimate_conflict=conflict))
    assert held.action == Action.HOLD  # a data disagreement is not a sell signal


# ------------------------------------------------------------------ restatement vintages & field merge
def _cf(val, end, start, filed, form="10-Q"):
    return {"val": val, "end": end, "start": start, "filed": filed, "form": form}


def _facts_with_cfo(cfo_items):
    rev = [_cf(100e9, e, s, fd) for e, s, fd in (("2024-03-31", "2024-01-01", "2024-05-01"), ("2024-06-30", "2024-04-01", "2024-08-01"))]
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}, "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": cfo_items}}}}}


def test_restated_operating_cash_flow_is_used_after_its_filing():
    """Evaluation 2: Operating Cash Flow restated 100 → 10 but the analysis kept using 100."""
    from marketlens.domain.fundamentals import FIRST_REPORTED, as_of
    from marketlens.providers.live.sec_edgar import parse_company_facts

    items = [
        _cf(100.0, "2024-03-31", "2024-01-01", "2024-05-01"),  # Q1 YTD as first reported
        _cf(250.0, "2024-06-30", "2024-01-01", "2024-08-01"),  # H1 YTD → Q2 = 150
        _cf(10.0, "2024-03-31", "2024-01-01", "2025-05-01"),  # Q1 restated in a later filing
    ]
    qs = parse_company_facts(_facts_with_cfo(items), "T")
    by = {q.period_end: q for q in qs}
    assert by[date(2024, 3, 31)].operating_cash_flow == 100.0  # first reported, never overwritten
    before = {q.period_end: q for q in as_of(qs, date(2025, 1, 1))}
    after = {q.period_end: q for q in as_of(qs, date(2025, 6, 1))}
    assert before[date(2024, 3, 31)].operating_cash_flow == 100.0 and before[date(2024, 6, 30)].operating_cash_flow == 150.0
    assert after[date(2024, 3, 31)].operating_cash_flow == 10.0 and "operating_cash_flow" in after[date(2024, 3, 31)].restated
    # the derived Q2 changes too: H1 250 − restated Q1 10 = 240, known only from the restatement date
    assert after[date(2024, 6, 30)].operating_cash_flow == 240.0
    research = {q.period_end: q for q in as_of(qs, date(2025, 6, 1), basis=FIRST_REPORTED)}
    assert research[date(2024, 3, 31)].operating_cash_flow == 100.0


def test_save_quarters_adds_fields_missing_from_the_stored_vintage(tmp_path):
    """Evaluation 2: a field absent from the first stored payload was never filled in later."""
    from marketlens.domain.fundamentals import QuarterlyFinancials, as_of
    from tests.acceptance_a_grade.test_research_integrity import _store

    st = _store(tmp_path)
    q0 = QuarterlyFinancials(date(2024, 3, 31), date(2024, 5, 1), "Q1", "sec-edgar", revenue=100.0)
    st.save_quarters("T", [q0])
    q1 = QuarterlyFinancials(date(2024, 3, 31), date(2024, 5, 1), "Q1", "sec-edgar", revenue=999.0, total_debt=50.0,
                             field_filed={"revenue": date(2024, 5, 1), "total_debt": date(2024, 6, 1)}, extras={"rpo": 7.0})
    st.save_quarters("T", [q1])
    from datetime import timedelta

    got = st.quarters("T", timedelta(days=1))[0]
    assert got.revenue == 100.0  # a stored value is never replaced by a re-fetch
    assert got.total_debt == 50.0 and got.field_filed["total_debt"] == date(2024, 6, 1) and got.extras["rpo"] == 7.0
    assert as_of([got], date(2024, 5, 15))[0].total_debt is None  # not yet public on that day


def test_derived_q4_and_total_debt_follow_restatements():
    from marketlens.domain.fundamentals import as_of
    from marketlens.providers.live.sec_edgar import parse_company_facts

    rev = [_cf(100.0, "2024-03-31", "2024-01-01", "2024-05-01"), _cf(100.0, "2024-06-30", "2024-04-01", "2024-08-01"),
           _cf(100.0, "2024-09-30", "2024-07-01", "2024-11-01"), _cf(500.0, "2024-12-31", "2024-01-01", "2025-02-15", "10-K"),
           _cf(460.0, "2024-12-31", "2024-01-01", "2026-02-15", "10-K")]  # FY restated a year later
    debt = [{"val": 40.0, "end": "2024-12-31", "filed": "2025-02-15", "form": "10-K"}, {"val": 30.0, "end": "2024-12-31", "filed": "2026-02-15", "form": "10-K"}]
    facts = {"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}, "LongTermDebt": {"units": {"USD": debt}}}}}
    qs = parse_company_facts(facts, "T")
    q4_2025 = next(q for q in as_of(qs, date(2025, 6, 1)) if q.period_end == date(2024, 12, 31))
    q4_2026 = next(q for q in as_of(qs, date(2026, 6, 1)) if q.period_end == date(2024, 12, 31))
    assert q4_2025.revenue == 200.0 and q4_2026.revenue == 160.0
    assert q4_2025.total_debt == 40.0 and q4_2026.total_debt == 30.0


def test_two_measures_listed_respectively_are_not_a_range():
    """Found while fixing evaluation 4 M1: 'GAAP and non-GAAP gross margins … 73.3% and 73.5%, respectively'
    was stored as one 73.3–73.5% range."""
    (m, lo, hi, st), = _g("GAAP and non-GAAP gross margins are expected to be 73.3% and 73.5%, respectively, plus or minus 50 basis points.")
    assert (lo, hi, st) == (None, None, "GUIDANCE_UNCLEAR")
    (m, lo, hi, st), = _g("For fiscal 2027, we expect GAAP EPS of $1.00 to $1.10 and non-GAAP EPS of $1.30 to $1.40.")
    assert (lo, hi, st) == (None, None, "GUIDANCE_UNCLEAR")


def test_total_debt_vintages_only_use_components_known_at_that_date():
    """Found by live-verify on real SEC data (2026-09-26, NVDA): 'max() arg is an empty sequence'. A later
    vintage date was evaluated with a debt component that was first filed even later."""
    from marketlens.domain.fundamentals import as_of
    from marketlens.providers.live.sec_edgar import parse_company_facts

    rev = [_cf(100.0, "2023-12-31", "2023-10-01", "2024-02-01")]
    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": rev}},
        "LongTermDebt": {"units": {"USD": [{"val": 40.0, "end": "2023-12-31", "filed": "2024-02-01", "form": "10-K"},
                                           {"val": 35.0, "end": "2023-12-31", "filed": "2024-08-01", "form": "10-Q"}]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [{"val": 30.0, "end": "2023-12-31", "filed": "2024-10-01", "form": "10-K/A"}]}},
    }}}
    qs = parse_company_facts(facts, "T")  # must not raise
    q = {x.period_end: x for x in as_of(qs, date(2024, 9, 1))}[date(2023, 12, 31)]
    assert q.total_debt == 35.0  # the restated LongTermDebt, known on 2024-08-01


def test_a_parser_crash_on_one_company_is_a_provider_error_not_a_sync_stopper(monkeypatch):
    from marketlens.providers.contracts import ProviderDataError
    from marketlens.providers.live import sec_edgar

    def boom(facts, ticker):  # noqa: ANN001, ANN202
        raise ValueError("max() arg is an empty sequence")

    try:
        sec_edgar._guarded(boom, {}, "XYZ")
        raise AssertionError("no error")
    except ProviderDataError as e:
        assert "XYZ" in str(e) and "max()" in str(e)
