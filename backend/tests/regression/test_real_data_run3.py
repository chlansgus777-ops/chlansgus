"""Live-verify run 36252886492 (every key configured): 11 of 13 categories VERIFIED; two failures reproduced here.

- earnings: Finnhub's free earnings calendar returned 0 rows for NVDA for 800 days AND for the last 35 days
  (NVDA reported on 2026-08-26) → the free path is now ``/stock/earnings`` (actual vs consensus per fiscal
  period, no date) paired with the SEC 8-K Item 2.02 acceptance time (the real announcement);
- estimates: Alpha Vantage answered, prefetch said "OK", yet nothing was stored — an answer whose rows are all
  skipped was an empty success. It is now an error that names what was seen."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import httpx
import pytest

from marketlens.domain.earnings import pair_with_releases
from marketlens.providers.contracts import ProviderDataError


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


ROWS = [  # NVIDIA-like fiscal periods (Finnhub gives the period end, never the report date)
    {"period": date(2025, 10, 26), "actual": 1.30, "estimate": 1.26, "quarter": 3, "year": 2026},
    {"period": date(2026, 1, 25), "actual": 1.62, "estimate": 1.53, "quarter": 4, "year": 2026},
    {"period": date(2026, 4, 26), "actual": 1.87, "estimate": 1.75, "quarter": 1, "year": 2027},
    {"period": date(2026, 7, 26), "actual": 2.05, "estimate": 1.98, "quarter": 2, "year": 2027},
]
TIMES = [_utc("2025-11-19T21:20:00"), _utc("2026-02-25T21:20:00"), _utc("2026-05-27T20:21:00"), _utc("2026-08-26T20:21:19"),
         _utc("2026-06-10T12:00:00")]  # a second Item 2.02 (e.g. an update) after Q1's release: not a new period


def test_each_period_is_dated_by_its_first_8k_release_never_by_the_period_end():
    reps = pair_with_releases(ROWS, TIMES, "finnhub+sec-8k")
    assert [r.report_date for r in reps] == [date(2025, 11, 19), date(2026, 2, 25), date(2026, 5, 27), date(2026, 8, 26)]
    assert reps[-1].eps_actual == 2.05 and reps[-1].eps_consensus == 1.98 and reps[-1].fiscal_label == "Q2 2027"
    assert all(r.source == "finnhub+sec-8k" for r in reps)


def test_a_period_without_a_release_is_dropped_and_the_new_york_date_is_used():
    # the release of the last period is not in the list (e.g. not yet on EDGAR) → that period is not reported
    reps = pair_with_releases(ROWS, TIMES[:3], "x")
    assert [r.fiscal_label for r in reps] == ["Q3 2026", "Q4 2026", "Q1 2027"]
    # 02:30 UTC on 11-20 is still 11-19 in New York
    assert pair_with_releases(ROWS[:1], [_utc("2025-11-20T02:30:00")], "x")[0].report_date == date(2025, 11, 19)
    # a release 90 days after the period is not its release
    assert pair_with_releases(ROWS[:1], [_utc("2026-01-30T21:00:00")], "x") == []


def _providers(earnings_rows: Any, calendar_rows: list[dict[str, Any]]) -> Any:
    from marketlens.providers.live.finnhub import FinnhubProvider
    from marketlens.providers.live.sec_edgar import SecEdgarProvider

    calls: list[str] = []

    def fh(req: httpx.Request) -> httpx.Response:
        calls.append(req.url.path)
        if req.url.path.endswith("/stock/earnings"):
            return httpx.Response(200, json=earnings_rows)
        if req.url.path.endswith("/calendar/earnings"):
            return httpx.Response(200, json={"earningsCalendar": calendar_rows})
        return httpx.Response(404)

    recent = {"form": ["8-K", "10-Q", "8-K", "8-K", "8-K"], "items": ["2.02,9.01", "", "5.02", "2.02,9.01", "2.02"],
              "filingDate": ["2026-08-26", "2026-08-26", "2026-06-01", "2026-05-27", "2026-02-25"],
              "accessionNumber": ["a1", "a2", "a3", "a4", "a5"],
              "acceptanceDateTime": ["2026-08-26T20:21:19.000Z", "2026-08-26T20:30:00.000Z", "2026-06-01T12:00:00.000Z", "2026-05-27T20:21:00.000Z", "2026-02-25T21:20:00.000Z"]}

    def sec(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("company_tickers_exchange.json"):
            return httpx.Response(200, json={"fields": ["cik", "name", "ticker", "exchange"], "data": [[1045810, "NVIDIA CORP", "NVDA", "Nasdaq"]]})
        if "/submissions/" in req.url.path:
            return httpx.Response(200, json={"filings": {"recent": recent}})
        return httpx.Response(404)

    return FinnhubProvider("k", transport=httpx.MockTransport(fh)), SecEdgarProvider("MarketLens t@example.com", transport=httpx.MockTransport(sec)), calls


def test_sec_release_times_are_only_item_2_02_8ks():
    _fh, sec, _ = _providers([], [])
    assert sec.earnings_release_times("NVDA", date(2026, 1, 1)) == [_utc("2026-02-25T21:20:00"), _utc("2026-05-27T20:21:00"), _utc("2026-08-26T20:21:19")]


def _data(fh: Any, sec: Any) -> Any:
    from marketlens.application.data_access import DataAccess

    class Chain:
        def __init__(self, providers: list[Any]) -> None:
            self.providers = providers

        def call(self, method: str, *a: Any, **k: Any) -> Any:
            from types import SimpleNamespace

            for p in self.providers:
                try:
                    return SimpleNamespace(value=getattr(p, method)(*a), provider=p.name, conflicts=[])
                except AttributeError:
                    continue
            raise AssertionError(method)

    class Reg:
        chains = {"analyst": Chain([fh]), "fundamental": Chain([sec])}

        def chain(self, k: str) -> Any:
            return self.chains[k]

    return DataAccess(Reg(), {}, store=None)


def test_the_free_path_pairs_finnhub_surprises_with_sec_release_times():
    rows = [{"period": "2026-04-26", "actual": 1.87, "estimate": 1.75, "quarter": 1, "year": 2027, "surprise": 0.12, "symbol": "NVDA"},
            {"period": "2026-07-26", "actual": 2.05, "estimate": 1.98, "quarter": 2, "year": 2027, "surprise": 0.07, "symbol": "NVDA"}]
    fh, sec, calls = _providers(rows, [])
    f = _data(fh, sec).earnings("NVDA")
    assert f.error is None and f.provider == "finnhub+sec-8k"
    assert [(r.report_date, r.eps_actual) for r in f.value] == [(date(2026, 5, 27), 1.87), (date(2026, 8, 26), 2.05)]
    assert not any(c.endswith("/calendar/earnings") for c in calls)  # no second Finnhub request on success


def test_both_free_sources_empty_is_missing_data_with_both_reasons():
    fh, sec, _ = _providers([], [])
    f = _data(fh, sec).earnings("NVDA")
    assert f.value is None and "earnings surprises" in f.error and "0건" in f.error


def test_alpha_vantage_answer_with_no_usable_row_is_an_error_not_an_empty_success():
    from marketlens.providers.live.alphavantage import AlphaVantageEstimatesProvider

    body = {"symbol": "NVDA", "estimates": [{"date": "2027-01-31", "horizon": "Fiscal Year 1", "eps_estimate_average": "7.1", "eps_estimate_analyst_count": "50",
                                             "eps_estimate_average_30_days_ago": "7.0", "revenue_estimate_average": "2.9e11"}]}
    av = AlphaVantageEstimatesProvider("k", transport=httpx.MockTransport(lambda req: httpx.Response(200, json=body)))
    with pytest.raises(ProviderDataError, match="fiscal year 1"):
        av.get_estimate_observations("NVDA", date(2026, 9, 26))


def test_alpha_vantage_horizon_spelling_variants_are_normalised_not_guessed():
    from marketlens.providers.live.alphavantage import parse_estimates

    rows = [{"date": "2027-01-31", "horizon": "Current_Fiscal_Year", "eps_estimate_average": "7.1"},
            {"date": "2026-10-31", "horizon": "  current  fiscal quarter ", "eps_estimate_average": "2.0"},
            {"date": "2026-10-31", "horizon": "next 12 months", "eps_estimate_average": "8.0"}]
    obs, issues = parse_estimates({"estimates": rows}, "NVDA", date(2026, 9, 26))
    assert sorted(o.horizon for o in obs) == ["current fiscal quarter", "current fiscal year"]
    assert any("next 12 months" in i for i in issues)  # reported, and live-verify fails on any contract issue


def _av_row(end: str, kind: str, eps: str) -> dict[str, Any]:
    return {"date": end, "horizon": kind, "eps_estimate_average": eps, "eps_estimate_high": eps, "eps_estimate_low": eps,
            "eps_estimate_analyst_count": "40", "eps_estimate_average_7_days_ago": eps, "eps_estimate_average_30_days_ago": eps,
            "eps_estimate_average_60_days_ago": eps, "eps_estimate_average_90_days_ago": eps, "eps_estimate_revision_up_trailing_7_days": "1",
            "eps_estimate_revision_down_trailing_7_days": "0", "eps_estimate_revision_up_trailing_30_days": "2",
            "eps_estimate_revision_down_trailing_30_days": "0", "revenue_estimate_average": "1e11", "revenue_estimate_high": "1.1e11",
            "revenue_estimate_low": "0.9e11", "revenue_estimate_analyst_count": "38"}


def test_the_real_alpha_vantage_shape_per_period_rows_labelled_by_date():
    """Run 36253463681: NVDA answered 41 rows, horizon only 'fiscal year' / 'fiscal quarter' plus the period end."""
    from marketlens.application.estimate_book import build
    from marketlens.providers.live.alphavantage import parse_estimates

    day = date(2026, 9, 26)
    rows = [_av_row("2019-01-27", "fiscal year", "1.0"), _av_row("2026-01-25", "fiscal year", "4.5"), _av_row("2027-01-31", "fiscal year", "7.10"),
            _av_row("2028-01-30", "fiscal year", "9.20"), _av_row("2029-01-28", "fiscal year", "11.0"),
            _av_row("2026-04-26", "fiscal quarter", "1.75"), _av_row("2026-07-26", "fiscal quarter", "1.98"),
            _av_row("2026-10-25", "fiscal quarter", "2.20"), _av_row("2027-01-31", "fiscal quarter", "2.45")]
    obs, issues = parse_estimates({"symbol": "NVDA", "estimates": rows}, "NVDA", day)
    assert issues == []
    by = {(o.period_type, o.period_end): o.horizon for o in obs}
    assert by[("annual", date(2027, 1, 31))] == "current fiscal year" and by[("annual", date(2028, 1, 30))] == "next fiscal year"
    assert by[("annual", date(2029, 1, 28))] == "fiscal year +2"
    assert by[("quarter", date(2026, 10, 25))] == "current fiscal quarter" and by[("quarter", date(2027, 1, 31))] == "next fiscal quarter"
    assert by[("quarter", date(2026, 7, 26))] == "recent fiscal quarter"  # ended 62 days ago: may still await its report
    assert ("annual", date(2019, 1, 27)) not in by and ("annual", date(2026, 1, 25)) not in by and ("quarter", date(2026, 4, 26)) not in by
    rep = build("NVDA", obs, day)  # the estimate book reads it: FY1 = the year ending 2027-01-31
    assert rep.snapshot is not None and rep.snapshot.analyst_count == 40 and rep.snapshot.forward_eps is not None
    assert 7.10 <= rep.snapshot.forward_eps <= 9.20
