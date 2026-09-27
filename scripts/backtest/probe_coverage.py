"""Backtest stage 0: how far back each free source answers, measured against the REAL providers (GitHub Actions).

Prints one JSON document (no secret is ever printed). Every probe records the request made, the HTTP status and
what came back (dates, counts). Polygon is called at most 5 times a minute (free tier), SEC at most 10 per second.

    python scripts/backtest/probe_coverage.py --out probe.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, timedelta
from typing import Any

import httpx

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
from marketlens.domain.market_calendar import is_trading_day  # noqa: E402

POLY = "https://api.polygon.io"
_last_poly = [0.0]


def _trading_day_on_or_before(d: date) -> date:
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def poly(client: httpx.Client, path: str, **params: Any) -> tuple[int, Any]:
    wait = 13.0 - (time.monotonic() - _last_poly[0])  # 5 requests / minute
    if wait > 0:
        time.sleep(wait)
    _last_poly[0] = time.monotonic()
    params["apiKey"] = os.environ["POLYGON_API_KEY"]
    r = client.get(POLY + path, params=params, timeout=60)
    try:
        body = r.json()
    except ValueError:
        body = {"text": r.text[:300]}
    if isinstance(body, dict):
        body.pop("request_id", None)
    return r.status_code, body


def sec(client: httpx.Client, url: str) -> tuple[int, Any]:
    time.sleep(0.15)  # well under 10 requests / second
    r = client.get(url, headers={"User-Agent": os.environ["SEC_USER_AGENT"], "Accept-Encoding": "gzip"}, timeout=60)
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, {"text": r.text[:300]}


def probe_polygon(client: httpx.Client, today: date) -> dict[str, Any]:
    out: dict[str, Any] = {"grouped_unadjusted": []}
    for years in (1, 1.9, 2.1, 3, 5, 10):
        d = _trading_day_on_or_before(today - timedelta(days=int(365.25 * years)))
        st, body = poly(client, f"/v2/aggs/grouped/locale/us/market/stocks/{d.isoformat()}", adjusted="false")
        out["grouped_unadjusted"].append({"years_back": years, "day": d.isoformat(), "http": st, "status": body.get("status"),
                                         "results": body.get("resultsCount"), "message": str(body.get("message") or body.get("error") or "")[:200]})
    for name, path, params in (
        ("dividends_oldest", "/v3/reference/dividends", {"order": "asc", "sort": "ex_dividend_date", "limit": 1}),
        ("dividends_2y_ago", "/v3/reference/dividends", {"ex_dividend_date.lte": (today - timedelta(days=730)).isoformat(), "order": "desc", "sort": "ex_dividend_date", "limit": 1}),
        ("splits_oldest", "/v3/reference/splits", {"order": "asc", "sort": "execution_date", "limit": 1}),
        ("tickers_delisted", "/v3/reference/tickers", {"market": "stocks", "active": "false", "limit": 1000}),
        ("tickers_on_date", "/v3/reference/tickers", {"market": "stocks", "date": (today - timedelta(days=5 * 365)).isoformat(), "limit": 5}),
    ):
        st, body = poly(client, path, **params)
        res = body.get("results") if isinstance(body, dict) else None
        rec: dict[str, Any] = {"http": st, "status": body.get("status") if isinstance(body, dict) else None, "count": len(res) if isinstance(res, list) else None,
                               "message": str((body or {}).get("message") or (body or {}).get("error") or "")[:200]}
        if isinstance(res, list) and res:
            if name.startswith("dividends"):
                rec["first"] = {k: res[0].get(k) for k in ("ticker", "ex_dividend_date", "cash_amount", "pay_date")}
            elif name.startswith("splits"):
                rec["first"] = {k: res[0].get(k) for k in ("ticker", "execution_date", "split_from", "split_to")}
            else:
                rec["with_cik"] = sum(1 for x in res if x.get("cik"))
                rec["with_delisted_utc"] = sum(1 for x in res if x.get("delisted_utc"))
                ds = sorted(x["delisted_utc"][:10] for x in res if x.get("delisted_utc"))
                rec["delisted_range"] = [ds[0], ds[-1]] if ds else None
                rec["has_more_pages"] = bool(body.get("next_url"))
        out[name] = rec
    return out


def probe_sec(client: httpx.Client) -> dict[str, Any]:
    out: dict[str, Any] = {}
    st, facts = sec(client, "https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json")
    filed = []
    if st == 200:
        for tax in facts.get("facts", {}).values():
            for concept in tax.values():
                for unit in concept.get("units", {}).values():
                    filed += [x.get("filed") for x in unit if x.get("filed")]
    out["companyfacts_AAPL"] = {"http": st, "facts": len(filed), "oldest_filed": min(filed) if filed else None, "newest_filed": max(filed) if filed else None,
                                "has_accn": st == 200 and "accn" in json.dumps(facts)[:200000]}
    st, sub = sec(client, "https://data.sec.gov/submissions/CIK0000320193.json")
    rec: dict[str, Any] = {"http": st}
    if st == 200:
        recent = sub["filings"]["recent"]
        forms = recent.get("form", [])
        acc = recent.get("acceptanceDateTime", [])
        dates = recent.get("filingDate", [])
        rec.update(recent_filings=len(forms), with_acceptance_time=sum(1 for a in acc if a), oldest_recent=min(dates) if dates else None,
                   older_pages=len(sub["filings"].get("files", [])))
        for f in ("10-Q", "10-K", "8-K", "4"):
            ds = [d for d, x in zip(dates, forms) if x == f]
            rec[f"oldest_{f}_in_recent"] = min(ds) if ds else None
            rec[f"count_{f}_in_recent"] = len(ds)
        if sub["filings"].get("files"):
            name = sub["filings"]["files"][-1]["name"]
            st2, old = sec(client, f"https://data.sec.gov/submissions/{name}")
            if st2 == 200:
                rec["oldest_page_first_date"] = min(old.get("filingDate", []) or [None])
                rec["oldest_page_acceptance_times"] = sum(1 for a in old.get("acceptanceDateTime", []) if a)
    out["submissions_AAPL"] = rec
    st, tick = sec(client, "https://www.sec.gov/files/company_tickers_exchange.json")
    out["company_tickers_exchange"] = {"http": st, "rows": len(tick.get("data", [])) if st == 200 else None,
                                       "note": "current ticker -> CIK only (no history)"}
    return out


def probe_fred(client: httpx.Client) -> dict[str, Any]:
    out: dict[str, Any] = {}
    key = os.environ.get("FRED_API_KEY")
    if not key:
        return {"skipped": "FRED_API_KEY not set"}
    for s in ("VIXCLS", "DGS10", "DTB3", "DEXKOUS", "CPIAUCSL"):
        time.sleep(0.6)
        r = client.get("https://api.stlouisfed.org/fred/series", params={"series_id": s, "api_key": key, "file_type": "json"}, timeout=60)
        info = (r.json().get("seriess") or [{}])[0] if r.status_code == 200 else {}
        out[s] = {"http": r.status_code, "observation_start": info.get("observation_start"), "observation_end": info.get("observation_end")}
    time.sleep(0.6)
    r = client.get("https://api.stlouisfed.org/fred/series/vintagedates", params={"series_id": "CPIAUCSL", "api_key": key, "file_type": "json", "limit": 1}, timeout=60)
    out["CPIAUCSL_first_vintage"] = {"http": r.status_code, "first": (r.json().get("vintage_dates") or [None])[0] if r.status_code == 200 else None}
    return out


def probe_finra(client: httpx.Client) -> dict[str, Any]:
    body = {"limit": 1, "sortFields": ["settlementDate"], "fields": ["settlementDate", "symbolCode"]}
    r = client.post("https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest", json=body, headers={"Accept": "application/json"}, timeout=60)
    try:
        rows = r.json()
    except ValueError:
        rows = []
    return {"http": r.status_code, "oldest": rows[0] if isinstance(rows, list) and rows else None}


def probe_finnhub(client: httpx.Client) -> dict[str, Any]:
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        return {"skipped": "FINNHUB_API_KEY not set"}
    out: dict[str, Any] = {}
    r = client.get("https://finnhub.io/api/v1/stock/earnings", params={"symbol": "AAPL", "token": key}, timeout=60)
    rows = r.json() if r.status_code == 200 else []
    out["earnings_surprises"] = {"http": r.status_code, "quarters": len(rows) if isinstance(rows, list) else None,
                                 "oldest_period": min((x.get("period") for x in rows), default=None) if isinstance(rows, list) else None,
                                 "note": "estimate per past quarter; whether it is the value as of that time is not stated"}
    time.sleep(1.1)
    old = (date.today() - timedelta(days=3 * 365)).isoformat()
    r = client.get("https://finnhub.io/api/v1/company-news", params={"symbol": "AAPL", "from": old, "to": (date.today() - timedelta(days=2 * 365 + 300)).isoformat(), "token": key}, timeout=60)
    rows = r.json() if r.status_code == 200 else []
    out["company_news_3y_ago"] = {"http": r.status_code, "items": len(rows) if isinstance(rows, list) else None}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    today = date.today()
    report: dict[str, Any] = {"run_date": today.isoformat()}
    with httpx.Client() as client:
        for name, fn in (("sec", lambda: probe_sec(client)), ("fred", lambda: probe_fred(client)), ("finra", lambda: probe_finra(client)),
                         ("finnhub", lambda: probe_finnhub(client)), ("polygon", lambda: probe_polygon(client, today))):
            try:
                report[name] = fn()
            except Exception as e:  # noqa: BLE001 - a probe failure is a finding, recorded as such
                report[name] = {"error": f"{type(e).__name__}: {str(e)[:300]}"}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1, ensure_ascii=False)
    print(json.dumps(report, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
