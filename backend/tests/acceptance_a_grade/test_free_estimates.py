"""A-grade acceptance: free consensus data — self-accumulated revisions, conflicts, contracts, budget."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from marketlens.application.estimate_book import build
from marketlens.domain.estimates import EstimateObservation, cross_check, self_revision
from marketlens.providers.contracts import RateLimited
from marketlens.providers.live.alphavantage import parse_estimates

T0 = date(2026, 6, 1)


def fh(day: date, eps: float, rev: float = 10e9) -> EstimateObservation:
    return EstimateObservation("ABC", "finnhub", "FQ2026Q3", "quarter", None, day, eps=eps, revenue=rev, report_date=date(2026, 11, 5))


def test_self_accumulated_revision_is_accumulating_until_enough_history_then_ready():
    hist = [fh(T0 + timedelta(days=i), 1.00 + 0.001 * i) for i in range(0, 50)]
    as_of = T0 + timedelta(days=49)
    r7 = self_revision(hist, "eps", as_of, 7)
    assert r7.status == "READY" and r7.basis == "SELF" and r7.value == pytest.approx(1.049 / 1.042 - 1)
    r90 = self_revision(hist, "eps", as_of, 90)
    assert r90.status == "ACCUMULATING" and r90.value is None and "49/90" in r90.detail  # never back-filled


def test_no_history_is_unknown_and_a_gap_is_not_bridged():
    assert self_revision([], "eps", T0, 30).status == "UNKNOWN"
    gap = [fh(T0, 1.0), fh(T0 + timedelta(days=40), 1.1)]
    r = self_revision(gap, "eps", T0 + timedelta(days=40), 30)  # nothing near day −30 → not "READY"
    assert r.status == "UNKNOWN" and r.value is None


def test_future_snapshots_are_invisible_point_in_time():
    hist = [fh(T0, 1.0), fh(T0 + timedelta(days=30), 2.0)]
    rep = build("ABC", hist, T0 + timedelta(days=3))
    assert rep.snapshot is not None and rep.snapshot.as_of == T0  # the later snapshot does not exist yet


def test_provider_disagreement_is_flagged_not_averaged():
    a = EstimateObservation("ABC", "alphavantage", "quarter:2026-09-30", "quarter", date(2026, 9, 30), T0, eps=1.00)
    b = EstimateObservation("ABC", "finnhub", "FQ2026Q3", "quarter", None, T0, eps=1.20)
    cc = cross_check(a, b)
    assert cc.status == "SEVERE_DATA_CONFLICT" and "alphavantage 1 vs finnhub 1.2" in cc.detail
    assert cross_check(a, EstimateObservation("ABC", "finnhub", "x", "quarter", None, T0, eps=1.05)).status == "DATA_CONFLICT"
    assert cross_check(a, None).status == "SINGLE_SOURCE"


def test_alpha_vantage_contract_checks_and_rate_limit_notice():
    with pytest.raises(RateLimited):
        parse_estimates({"Information": "Thank you for using Alpha Vantage! Our standard API rate limit is 25 requests per day."}, "IBM", T0)
    obs, issues = parse_estimates({"estimates": [{"date": "2026-12-31", "horizon": "current fiscal year", "eps_estimate_average": "None"}]}, "IBM", T0)
    assert obs[0].eps is None  # "None" string is missing, never 0
    assert issues and "eps_estimate_analyst_count" in issues[0]  # a changed contract is reported, not ignored
    obs, _ = parse_estimates({"estimates": [{"date": "2026-12-31", "horizon": "some new horizon", "eps_estimate_average": "1"}]}, "IBM", T0)
    assert obs == []  # unknown horizons are skipped, not guessed


def test_prefetch_respects_the_daily_budget_and_cache(tmp_path):
    from marketlens.application.data_access import DataAccess
    from marketlens.application.market_store import MarketStore
    from marketlens.infrastructure.db.models import Base
    from marketlens.infrastructure.db.session import make_engine, make_session_factory

    calls: list[str] = []

    class FakeAV:
        name, configured = "alphavantage", True

        def get_estimate_observations(self, t: str, day: date) -> list[EstimateObservation]:
            calls.append(t)
            return [EstimateObservation(t, "alphavantage", "annual:2027-01-31", "annual", date(2027, 1, 31), day, eps=1.0)]

    class Chain:
        providers = [FakeAV()]

    class Reg:
        mode = None

        def chain(self, _k: str) -> Chain:
            return Chain()

    eng = make_engine(f"sqlite:///{(tmp_path / 'e.db').as_posix()}")
    Base.metadata.create_all(eng)
    da = DataAccess(Reg(), {}, store=MarketStore(make_session_factory(eng), "LIVE"))  # type: ignore[arg-type]
    out = da.prefetch_estimates(["A", "B", "C"], T0, budget=2, ttl_days=3)
    assert calls == ["A", "B"] and out["C"].startswith("일일 무료 한도")
    out2 = da.prefetch_estimates(["A", "C"], T0 + timedelta(days=1), budget=2, ttl_days=3)
    assert calls == ["A", "B", "C"] and out2["A"].startswith("캐시 사용")  # cached A is not re-requested
