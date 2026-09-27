"""Round 6 follow-ups: the edges of the K1–K5 fixes that the evaluator's counterexamples do not cover."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from tests.acceptance_a_grade.test_evaluation6_counterexamples import _fact, _nvda_fy2025_facts, _sec, _sessions, _store

UTC = timezone.utc


# ---------------------------------------------------------------- K1 guidance
@pytest.mark.parametrize("sentence,metric,lo", [
    ("For the fourth quarter, we expect revenue of $10.2 billion to $10.4 billion.", "revenue", 10.2e9),
    ("For fiscal 2027, we expect diluted EPS of $1.10 to $1.20.", "eps", 1.10),
    ("We expect fourth quarter gross margin of 45% to 46%.", "gross_margin", 0.45),
])
def test_a_plain_level_is_still_extracted(sentence, metric, lo):
    from marketlens.domain.guidance import extract

    (item,) = extract(sentence)
    assert item.metric == metric and item.status == "EXTRACTED" and item.low == pytest.approx(lo)


@pytest.mark.parametrize("sentence", [
    "We expect revenue of $10.2 billion to $10.4 billion, an increase of 12% year over year.",  # a level with a change note
    "We expect a foreign exchange headwind to fourth quarter revenue of $80 million.",
    "The acquisition is expected to be accretive to fiscal 2027 EPS by $0.05.",
])
def test_a_sentence_carrying_a_change_is_never_a_level(sentence):
    """Conservative by design: a level written together with a change is left out (value missing, never wrong)."""
    from marketlens.domain.guidance import extract

    assert all(i.status != "EXTRACTED" for i in extract(sentence))


def test_a_parenthesised_percentage_and_a_negated_loss_are_never_a_wrong_sign():
    from marketlens.domain.guidance import extract

    (m,) = extract("For fiscal 2027, we expect operating margin of (2%) to (1%).")
    assert m.status == "GUIDANCE_UNCLEAR"
    (e,) = extract("We no longer expect a net loss per share; we expect $0.05 to $0.10 per share for fiscal 2027.")
    assert e.status == "GUIDANCE_UNCLEAR" or (e.low is not None and e.low > 0)


# ---------------------------------------------------------------- K2 Q4 basis
def test_a_reverse_split_inside_the_year_puts_the_quarters_on_the_annual_basis():
    """1-for-5 reverse split after Q1: Q1 EPS 0.20 on 500 shares = 1.00 on the 100-share basis of the 10-K."""
    from marketlens.providers.live.sec_edgar import parse_company_facts

    q = [("2024-03-31", "2024-01-01", "2024-05-01"), ("2024-06-30", "2024-04-01", "2024-08-01"), ("2024-09-30", "2024-07-01", "2024-11-01")]
    fy = ("2024-12-31", "2024-01-01", "2025-02-15", "10-K")
    eps = [_fact(v, *p) for v, p in zip((0.20, 1.0, 1.0), q)] + [_fact(4.0, *fy)]
    sh = [_fact(v, *p) for v, p in zip((500.0, 100.0, 100.0), q)] + [_fact(100.0, *fy)]
    rev = [_fact(10.0, *p) for p in q] + [_fact(40.0, *fy)]
    qs = parse_company_facts({"facts": {"us-gaap": {"Revenues": {"units": {"USD": rev}}, "EarningsPerShareDiluted": {"units": {"USD/shares": eps}},
                                                    "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": sh}}}}}, "R")
    q4 = next(x for x in qs if x.period_end == date(2024, 12, 31))
    assert q4.eps_diluted == pytest.approx(4.0 - (1.0 + 1.0 + 1.0))


def test_an_unclear_share_basis_leaves_q4_eps_empty_instead_of_guessing():
    """Q1 shares 3.3x the annual count: not a clean split ratio → no Q4 EPS (revenue Q4 is still derived)."""
    from marketlens.providers.live.sec_edgar import parse_company_facts

    f = _nvda_fy2025_facts()
    f["facts"]["us-gaap"]["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"][0]["val"] = 7.5e9
    q4 = next(x for x in parse_company_facts(f, "NVDA") if x.period_end == date(2025, 1, 26))
    assert q4.eps_diluted is None and q4.revenue == pytest.approx(130.5e9 - 91.1e9)


def test_the_nvidia_split_year_gives_the_reported_q4_eps():
    from tests.acceptance_a_grade.test_evaluation6_counterexamples import _nvda_view_on

    q4 = next(q for q in _nvda_view_on(date(2025, 3, 15)) if q.period_end == date(2025, 1, 26))
    assert q4.eps_diluted == pytest.approx(0.89, abs=0.01)  # NVIDIA reported 0.89


def test_a_recast_10k_keeps_the_quarter_listed_with_unknown_values():
    """The Q4 of a recast year is not dropped (that would read as "no quarter"): it is listed, values empty,
    so TTM figures are missing rather than wrong."""
    from marketlens.domain.fundamentals import as_of, compute_metrics
    from tests.acceptance_a_grade.test_evaluation6_counterexamples import _discontinued_ops_facts
    from marketlens.providers.live.sec_edgar import parse_company_facts

    qs = as_of(parse_company_facts(_discontinued_ops_facts(), "DDD"), date(2025, 3, 1))
    q4 = next(q for q in qs if q.period_end == date(2024, 12, 31))
    assert q4.revenue is None and q4.operating_income is None and q4.filed_date == date(2025, 2, 15)
    assert compute_metrics(qs).revenue_ttm is None


# ---------------------------------------------------------------- K3 concept choice
def test_the_first_reported_concept_owns_a_period_and_later_comparatives_do_not_mix_in():
    from marketlens.domain.fundamentals import as_of
    from tests.acceptance_a_grade.test_evaluation6_counterexamples import _concept_switch_facts
    from marketlens.providers.live.sec_edgar import parse_company_facts

    qs = parse_company_facts(_concept_switch_facts(), "T")
    later = {q.period_end: q for q in as_of(qs, date(2025, 6, 1))}
    assert later[date(2024, 3, 31)].revenue == 100.0 and later[date(2025, 3, 31)].revenue == 110.0


# ---------------------------------------------------------------- K4 release date
def test_the_periodic_report_bounds_the_release_and_a_pre_announcement_never_dates_it():
    from marketlens.domain.earnings import pair_with_releases

    rows = [{"period": date(2025, 12, 31), "actual": 1.20, "estimate": 1.10, "quarter": 4, "year": 2025}]
    pre, full, later = datetime(2026, 1, 12, 13, tzinfo=UTC), datetime(2026, 2, 5, 21, 5, tzinfo=UTC), datetime(2026, 3, 2, 12, tzinfo=UTC)
    tenk = datetime(2026, 2, 20, 21, tzinfo=UTC)
    (r,) = pair_with_releases(rows, [pre, full, later], "x", [tenk])
    assert r.report_date == date(2026, 2, 5)  # not the pre-announcement, not the update after the 10-K


# ---------------------------------------------------------------- K5 relist evidence
def test_a_relist_is_not_decided_while_almost_none_of_the_absence_is_loaded(tmp_path):
    """Nothing of a nine-week absence is loaded yet: the question stays open (row untouched), and the next sync
    with the days loaded decides it."""
    from marketlens.domain.market import Bar

    st = _store(tmp_path)
    d1, d2 = date(2026, 6, 1), date(2026, 6, 2)
    st.sync_universe([_sec("TTT", 3)], d1)
    st.sync_universe([], d2)
    absence = set(_sessions(d2, date(2026, 8, 4)))
    out = st.sync_universe([_sec("TTT", 3)], date(2026, 8, 5), unloaded=absence)
    assert out["relisted"] == 0 and not any(s.ticker == "TTT" for s in st.securities(date(2026, 8, 5)))
    for d in absence:
        st.save_grouped(d, {"TTT": Bar(d, 50, 51, 49, 50, 1e6)}, "polygon")
    out = st.sync_universe([_sec("TTT", 3)], date(2026, 8, 6))
    assert out["relisted"] == 0 and any(s.ticker == "TTT" for s in st.securities(date(2026, 8, 6)))


# ---------------------------------------------------------------- observations
def test_earnings_without_the_sec_user_agent_is_a_credential_block():
    """Evaluation 6 re-run without SEC_USER_AGENT: earnings read as FAILED ("0 rows") instead of the real cause."""
    from marketlens.application.data_access import DataAccess
    from marketlens.application.live_verify import _classify
    from marketlens.providers.contracts import ProviderError

    class Fh:
        name, configured = "finnhub", True

        def get_earnings_surprises(self, t):  # noqa: ANN001, ANN201
            return []

        def get_earnings_history(self, t):  # noqa: ANN001, ANN201
            raise ProviderError("earnings history: NVDA 응답 0건")

    class Sec:
        name, configured = "sec-edgar", False

        def earnings_release_times(self, *a):  # noqa: ANN002, ANN201
            raise AssertionError("not configured")

    class Chain:
        def __init__(self, ps):  # noqa: ANN001
            self.providers = ps

        def call(self, method, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN201
            getattr(self.providers[0], method)(*a)

    class Reg:
        def chain(self, k):  # noqa: ANN001, ANN201
            return Chain([Fh()] if k == "analyst" else [Sec()])

    f = DataAccess(Reg(), {}, store=None).earnings("NVDA")
    assert f.value is None and _classify(ProviderError(f.error)) == "BLOCKED_BY_CREDENTIAL", f.error


def test_the_suite_never_reaches_a_real_provider():
    import httpx

    with pytest.raises(httpx.ConnectError, match="never call real providers"):
        httpx.get("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest")


def test_a_live_registry_without_a_transport_is_offline_in_tests():
    """The LIVE service test builds real providers; FINRA needs no key, so without the guard it would call out."""
    from marketlens.application.registry import build_live_registry
    from marketlens.config import Settings
    from marketlens.domain.enums import DataMode

    reg = build_live_registry(Settings(mode=DataMode.LIVE, database_url="sqlite:///:memory:", sec_user_agent=None, llm_provider="none"))
    finra = reg.chain("short_interest").providers[0]
    from marketlens.providers.contracts import ProviderError

    with pytest.raises(ProviderError):
        finra.get_short_interest("NVDA", date(2026, 9, 26))


_ = timedelta  # imported for symmetry with the counterexample helpers


# ---------------------------------------------------------------- found by live-verify run 36281358983 (00:04 UTC)
def test_fred_vintage_never_runs_ahead_of_freds_own_date():
    """At 00:04 UTC on 09-27 it is still 09-26 in St. Louis: FRED refuses realtime dates after its own today (400)."""
    import httpx

    from marketlens.providers.live.fred import FredMacroProvider

    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        rs = req.url.params["realtime_start"]
        seen.append(rs)
        if rs > "2026-09-26":
            return httpx.Response(400, json={"error_message": "Variable realtime_start can not be after today's date (2026-09-26)"})
        return httpx.Response(200, json={"observations": [{"date": "2026-09-24", "value": "3.88"}]})

    fred = FredMacroProvider("k", transport=httpx.MockTransport(handler), rate_per_s=1000.0)
    got = fred.get_series(["FED_FUNDS"], datetime(2026, 9, 27, 0, 4, tzinfo=UTC))
    assert seen and max(seen) <= "2026-09-26" and got["FED_FUNDS"].latest.value == 3.88
