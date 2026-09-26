"""Failures seen only on REAL provider data (GitHub-runner live-verify run 36251788168), reproduced offline.

- fundamentals: NVDA's latest quarter had revenue None — the preferred revenue concept existed in its facts
  but only for old periods, and the parser took ONE concept for all periods;
- eps_ttm: None for NVDA, AAPL and MSFT — 10-Ks tag only full-year diluted shares, so Q4 EPS (net income /
  Q4 shares) was never derived and every TTM EPS / P/E was missing;
- earnings: Finnhub answered an empty list and the check crashed with IndexError;
- guidance: the 8-K press release was not found — NVIDIA names its Exhibit 99.1 "q2fy26pr.htm", which no
  file-name pattern matches (the filing index page carries the exhibit TYPE)."""

from __future__ import annotations

from datetime import date
from typing import Any

import httpx
import pytest

from marketlens.domain.fundamentals import as_of, compute_metrics
from marketlens.providers.contracts import ProviderDataError
from marketlens.providers.live.sec_edgar import parse_company_facts, pick_exhibit_99, pick_press_release


def _f(val: float, start: str | None, end: str, filed: str, form: str = "10-Q") -> dict[str, Any]:
    d = {"val": val, "end": end, "filed": filed, "form": form}
    if start:
        d["start"] = start
    return d


# fiscal year = calendar 2025; Q1–Q3 in 10-Qs, the year (and Q4 only inside it) in the 10-K of 2026-02-20
Q = [("2025-01-01", "2025-03-31", "2025-04-30"), ("2025-04-01", "2025-06-30", "2025-07-30"), ("2025-07-01", "2025-09-30", "2025-10-30")]
FY = ("2025-01-01", "2025-12-31", "2026-02-20")


def _facts(**gaap: Any) -> dict[str, Any]:
    return {"facts": {"us-gaap": gaap}}


def _usd(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"units": {"USD": rows}}


def test_revenue_concept_is_chosen_per_period_not_once_for_all_periods():
    """The preferred concept (ASC 606 revenue) holds only 2019 periods; the company reports ``Revenues`` since.
    Before: the latest quarters had revenue None (the real NVDA live-verify result)."""
    old = [_f(7.0e9, "2019-01-01", "2019-03-31", "2019-04-30")]
    new = [_f(v, s, e, fd) for v, (s, e, fd) in zip((10e9, 11e9, 12e9), Q)]
    both = [_f(99e9, "2019-01-01", "2019-03-31", "2020-04-30")]  # 2019 Q1 also under Revenues (later comparative)
    ni = [_f(v, s, e, fd) for v, (s, e, fd) in zip((1e9, 1e9, 1e9), Q)] + [_f(1e9, "2019-01-01", "2019-03-31", "2019-04-30")]
    qs = parse_company_facts(_facts(RevenueFromContractWithCustomerExcludingAssessedTax=_usd(old), Revenues=_usd(new + both), NetIncomeLoss=_usd(ni)), "X")
    by_end = {q.period_end: q for q in qs}
    assert by_end[date(2025, 9, 30)].revenue == 12e9
    assert by_end[date(2025, 6, 30)].revenue == 11e9
    # a period both concepts report is taken from the preferred one only (never mixed, no false "restatement")
    assert by_end[date(2019, 3, 31)].revenue == 7.0e9 and "revenue" not in by_end[date(2019, 3, 31)].revisions


def _eps_facts(q4_shares: bool) -> dict[str, Any]:
    ni_q, eps_q, sh_q = (100.0, 110.0, 120.0), (1.00, 1.10, 1.20), (100.0, 100.0, 100.0)
    ni = [_f(v, s, e, fd) for v, (s, e, fd) in zip(ni_q, Q)] + [_f(460.0, FY[0], FY[1], FY[2], "10-K")]
    eps = [{**_f(v, s, e, fd)} for v, (s, e, fd) in zip(eps_q, Q)] + [_f(4.62, FY[0], FY[1], FY[2], "10-K")]
    sh = [_f(v, s, e, fd) for v, (s, e, fd) in zip(sh_q, Q)] + [_f(101.0, FY[0], FY[1], FY[2], "10-K")]
    if q4_shares:
        sh.append(_f(104.0, "2025-10-01", "2025-12-31", FY[2], "10-K"))
    rev = [_f(v * 10, s, e, fd) for v, (s, e, fd) in zip(ni_q, Q)] + [_f(4600.0, FY[0], FY[1], FY[2], "10-K")]
    return _facts(Revenues=_usd(rev), NetIncomeLoss=_usd(ni), EarningsPerShareDiluted={"units": {"USD/shares": eps}},
                  WeightedAverageNumberOfDilutedSharesOutstanding={"units": {"shares": sh}})


def test_q4_eps_is_derived_from_the_annual_eps_when_the_10k_has_no_q4_share_count():
    """Real 10-Ks (NVDA, AAPL, MSFT) tag only full-year diluted shares → Q4 EPS, TTM EPS and P/E were None."""
    qs = parse_company_facts(_eps_facts(q4_shares=False), "X")
    q4 = next(q for q in qs if q.period_end == date(2025, 12, 31))
    assert q4.eps_diluted == pytest.approx(4.62 - (1.00 + 1.10 + 1.20))
    assert q4.field_filed["eps_diluted"] == date(2026, 2, 20)  # known only from the 10-K on
    m = compute_metrics(as_of(qs, date(2026, 3, 1)))
    assert m.eps_ttm == pytest.approx(4.62)
    # point in time: before the 10-K there is no Q4 and hence no TTM of four quarters
    assert compute_metrics(as_of(qs, date(2026, 2, 19))).eps_ttm is None


def test_q4_eps_prefers_net_income_over_q4_shares_when_the_10k_tags_them():
    qs = parse_company_facts(_eps_facts(q4_shares=True), "X")
    q4 = next(q for q in qs if q.period_end == date(2025, 12, 31))
    assert q4.eps_diluted == pytest.approx((460.0 - 330.0) / 104.0)


NVDA_INDEX = """<table class="tableFile" summary="Document Format Files">
<tr><th scope="col">Seq</th><th scope="col">Description</th><th scope="col">Document</th><th scope="col">Type</th><th scope="col">Size</th></tr>
<tr><td scope="row">1</td><td scope="row">8-K</td><td scope="row"><a href="/ix?doc=/Archives/edgar/data/1045810/000104581025000207/nvda-20250827.htm">nvda-20250827.htm</a> &nbsp;iXBRL</td><td scope="row">8-K</td><td scope="row">39011</td></tr>
<tr class="evenRow"><td scope="row">2</td><td scope="row">CFO COMMENTARY</td><td scope="row"><a href="/Archives/edgar/data/1045810/000104581025000207/q2fy26cfocommentar.htm">q2fy26cfocommentar.htm</a></td><td scope="row">EX-99.2</td><td scope="row">5</td></tr>
<tr><td scope="row">3</td><td scope="row">PRESS RELEASE</td><td scope="row"><a href="/Archives/edgar/data/1045810/000104581025000207/q2fy26pr.htm">q2fy26pr.htm</a></td><td scope="row">EX-99.1</td><td scope="row">9</td></tr>
<tr><td scope="row">4</td><td scope="row">GRAPHIC</td><td scope="row"><a href="/Archives/edgar/data/1045810/000104581025000207/nvlogo.jpg">nvlogo.jpg</a></td><td scope="row">GRAPHIC</td><td scope="row">1</td></tr>
</table>"""


def test_exhibit_99_1_is_found_by_its_type_not_its_file_name():
    assert pick_press_release({"directory": {"item": [{"name": "nvda-20250827.htm"}, {"name": "q2fy26pr.htm"}, {"name": "q2fy26cfocommentar.htm"}]}}) is None  # the old way
    assert pick_exhibit_99(NVDA_INDEX) == "q2fy26pr.htm"
    assert pick_exhibit_99(NVDA_INDEX.replace("EX-99.1", "EX-10.1")) == "q2fy26cfocommentar.htm"  # no 99.1 → any 99.x
    assert pick_exhibit_99("<html>not an index</html>") is None and pick_exhibit_99("") is None


def _sec(index_status: int) -> Any:
    from marketlens.providers.live.sec_edgar import SecEdgarProvider

    sub = {"filings": {"recent": {"form": ["8-K"], "items": ["2.02,9.01"], "filingDate": ["2025-08-27"], "accessionNumber": ["0001045810-25-000207"],
                                   "acceptanceDateTime": ["2025-08-27T20:21:35.000Z"]}}}
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        p = req.url.path
        seen.append(p)
        if p.endswith("company_tickers_exchange.json"):
            return httpx.Response(200, json={"fields": ["cik", "name", "ticker", "exchange"], "data": [[1045810, "NVIDIA CORP", "NVDA", "Nasdaq"]]})
        if "/submissions/" in p:
            return httpx.Response(200, json=sub)
        if p.endswith("-index.htm"):
            return httpx.Response(index_status, text=NVDA_INDEX)
        if p.endswith("/index.json"):
            return httpx.Response(200, json={"directory": {"item": [{"name": "nvda-20250827.htm"}, {"name": "nvda-ex991.htm"}]}})
        if p.endswith(("q2fy26pr.htm", "nvda-ex991.htm")):
            return httpx.Response(200, text="<p>Revenue is expected to be $54.0 billion, plus or minus 2%.</p>")
        return httpx.Response(404)

    return SecEdgarProvider("MarketLens test@example.com", transport=httpx.MockTransport(handler)), seen


def test_earnings_release_text_is_fetched_for_a_free_form_exhibit_name():
    sec, seen = _sec(200)
    rel = sec.earnings_releases("NVDA", date(2025, 3, 1))
    assert len(rel) == 1 and rel[0]["url"].endswith("/q2fy26pr.htm") and "54.0 billion" in rel[0]["text"]
    assert any(p.endswith("0001045810-25-000207-index.htm") for p in seen)


def test_a_missing_index_page_falls_back_to_the_file_name_patterns():
    sec, _ = _sec(404)
    rel = sec.earnings_releases("NVDA", date(2025, 3, 1))
    assert len(rel) == 1 and rel[0]["url"].endswith("/nvda-ex991.htm") and rel[0]["text"]


def test_an_empty_finnhub_earnings_history_is_missing_data_not_a_crash():
    from marketlens.providers.live.finnhub import FinnhubProvider

    fh = FinnhubProvider("k", transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"earningsCalendar": []})))
    with pytest.raises(ProviderDataError, match="0건"):
        fh.get_earnings_history("NVDA")


def test_live_verify_reports_an_empty_earnings_answer_instead_of_crashing(monkeypatch, live):  # noqa: F811
    """Before: ``e[-1]`` on an empty list → 'IndexError: list index out of range' as the whole note."""
    from marketlens.application import live_verify
    from marketlens.application.data_access import DataAccess, Fetched

    monkeypatch.setattr(DataAccess, "earnings", lambda self, t: Fetched(value=[], provider="finnhub"))
    svc, _sync, _scan, _seen = live
    rep = live_verify.verify(svc, tickers=("NVDA", "JPM", "TSM"), record=False)
    e = rep["categories"]["earnings"]
    assert e["status"] == "FAILED" and "IndexError" not in e["note"] and "빈 목록" in e["note"], e
    assert "35일" in e["note"]  # the diagnostic probe of a short recent window ran


def test_live_verify_eps_ttm_is_verified_on_the_fixture_filings(live):  # noqa: F811
    """The eps_ttm category end to end. Note: the fixtures' 10-Ks DO tag a Q4 diluted share count (unlike the
    real ones), so this passes on the old code too; the real-shape case is the parser test above."""
    from marketlens.application import live_verify

    svc, _sync, _scan, _seen = live
    rep = live_verify.verify(svc, tickers=("NVDA", "JPM", "TSM"), record=False)
    assert rep["categories"]["eps_ttm"]["status"] == "VERIFIED", rep["categories"]["eps_ttm"]


from tests.integration.test_live_fixture_pipeline import live  # noqa: E402,F401
