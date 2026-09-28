"""Free-data feasibility probe (docs/freedata/FREE_DATA_FEASIBILITY.md): what the FREE official sources actually return.

Every answer is judged from the real response — an HTTP 200 carrying an error message, an empty array or a missing
page is never counted as success. Secrets come from the environment and are never printed (only "set"/"not set").
No new subscription, no multi-key rotation, no rate-limit evasion: each source is called within its published limit.

    python scripts/freedata/probe_free_sources.py --out free_probe.json

Sources (docs checked 2026-09-28, see the owner's instruction §7):
- Alpaca Market Data v2 historical bars, feed=sip, adjustment=raw (Basic plan: free, history from 2016, 200 req/min)
- Alpha Vantage LISTING_STATUS (active / delisted on a date since 2010; 25 requests/day on the free key)
- Nasdaq Trader symbol directory (nasdaqlisted.txt / otherlisted.txt, current listings)
- FINRA daily short-sale VOLUME files (CNMS since 2018-08-01) — short interest is a different dataset (API key)
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

ALPACA = "https://data.alpaca.markets/v2/stocks/bars"
AV = "https://www.alphavantage.co/query"

# boundary cases (owner's instruction §3.A: survivors, delistings, mergers, ticker changes, share classes, ADRs,
# reverse splits) — each checked for first/last day and gaps, never generalised from one success
CASES = [
    ("AAPL", "survivor, 4:1 split 2020-08-31"), ("MSFT", "survivor"), ("SPY", "ETF benchmark"),
    ("NVDA", "10:1 split 2024-06-10"), ("TSLA", "3:1 split 2022-08-25"), ("GOOGL", "class A"), ("GOOG", "class C"),
    ("BRK.B", "class B, dot ticker"), ("BF.B", "class B, dot ticker"), ("META", "renamed from FB 2022-06-09"), ("FB", "old ticker of META"),
    ("TWTR", "taken private 2022-10-27 (delisted)"), ("ATVI", "acquired by MSFT 2023-10-13"), ("XLNX", "acquired by AMD 2022-02-14"),
    ("SIVB", "bank failure 2023-03, delisted"), ("FRC", "bank failure 2023-05, delisted"), ("BBBY", "bankruptcy 2023, OTC"),
    ("GE", "reverse split 1:8 2021-08-02, spin-offs"), ("C", "survivor"), ("TSM", "ADR"), ("BABA", "ADR"), ("SHOP", "foreign, 10:1 split 2022"),
    ("CELG", "acquired by BMY 2019-11-20"), ("RTN", "merged into RTX 2020-04-03"), ("UTX", "renamed RTX 2020-04-03"),
    ("DWDP", "DowDuPont 2017–2019"), ("KHC", "merger 2015-07"), ("WBA", "taken private 2025"), ("X", "acquired 2025"), ("AMC", "reverse split 2023-08-24"),
]


def _av_csv(c: httpx.Client, state: str) -> list[dict[str, str]]:
    """Two back-to-back calls answered '{}' (HTTP 200): the free key's burst limit — space the calls, retry once."""
    key = os.environ.get("ALPHAVANTAGE_API_KEY") or ""
    for wait in (15, 65):
        time.sleep(wait)
        r = c.get(AV, params={"function": "LISTING_STATUS", "state": state, "apikey": key}, timeout=60)
        if r.status_code == 200 and not r.text.lstrip().startswith("{"):
            return list(csv.DictReader(io.StringIO(r.text)))
    raise RuntimeError(f"Alpha Vantage LISTING_STATUS {state}: HTTP {r.status_code} {r.text[:120].replace(key, '<key>')}")


def _nasdaq_current(c: httpx.Client) -> set[str]:
    """Current listings from the Nasdaq Trader directory (ACT/Nasdaq symbols, class suffix written with a dot)."""
    out: set[str] = set()
    for name, col in (("nasdaqlisted.txt", 0), ("otherlisted.txt", 0)):
        r = c.get(f"https://www.nasdaqtrader.com/dynamic/SymDir/{name}", timeout=60)
        for ln in r.text.splitlines()[1:]:
            f = ln.split("|")
            if len(f) > 3 and not ln.startswith("File Creation"):
                out.add(f[col].replace("-", ".").upper())
    return out


def _bars_one(c: httpx.Client, sym: str, start: str, end: str, asof: str | None) -> list[dict[str, Any]]:
    headers = {"APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"], "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"]}
    params: dict[str, Any] = {"symbols": sym, "timeframe": "1Day", "start": start, "end": end, "feed": "sip", "adjustment": "raw", "limit": 10000}
    if asof:
        params["asof"] = asof
    out: list[dict[str, Any]] = []
    token = None
    for _ in range(20):
        if token:
            params["page_token"] = token
        time.sleep(0.35)  # ≤ 200 requests/minute
        r = c.get(ALPACA, params=params, headers=headers, timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"alpaca {sym}: HTTP {r.status_code} {r.text[:120]}")
        body = r.json()
        out += (body.get("bars") or {}).get(sym, [])
        token = body.get("next_page_token")
        if not token:
            break
    return out


def _gaps(bars: list[dict[str, Any]], days: int = 40) -> list[tuple[str, str]]:
    ds = [datetime.fromisoformat(b["t"].replace("Z", "+00:00")).date() for b in bars]
    return [(a.isoformat(), b.isoformat()) for a, b in zip(ds, ds[1:]) if (b - a).days > days]


def alpaca_delisted_and_reused(c: httpx.Client, sample: int = 150, seed: int = 20260928) -> dict[str, Any]:
    """Delisted stocks (Alpha Vantage delisted list, delisted 2016-02 or later): does Alpaca return their history up to
    the delisting (asof = the day before)? Reused tickers (delisted AND listed again today): does the default request
    splice two companies into one series (a gap of more than 40 days inside one symbol's bars)?"""
    import random

    delisted = [r for r in _av_csv(c, "delisted") if r.get("assetType") == "Stock" and (r.get("delistingDate") or "") >= "2016-02-01"]
    active = _nasdaq_current(c)
    reused = sorted({r["symbol"] for r in delisted if r["symbol"].replace("-", ".").upper() in active})
    rng = random.Random(seed)
    pool = [r for r in delisted if r["symbol"].replace("-", ".").upper() not in active]
    picks = rng.sample(pool, min(sample, len(pool)))
    rows, ok, near_end = [], 0, 0
    for r in picks:
        d = r["delistingDate"]
        asof = (datetime.fromisoformat(d) - timedelta(days=1)).date().isoformat()
        start = max("2016-01-01", r.get("ipoDate") or "2016-01-01")
        try:
            bars = _bars_one(c, r["symbol"], start, d, asof)
        except RuntimeError as e:
            rows.append({"symbol": r["symbol"], "delisted": d, "error": str(e)[:120]})
            continue
        last = bars[-1]["t"][:10] if bars else None
        got = bool(bars)
        ok += got
        near = got and (datetime.fromisoformat(d) - datetime.fromisoformat(last)).days <= 7
        near_end += near
        rows.append({"symbol": r["symbol"], "exchange": r.get("exchange"), "delisted": d, "bars": len(bars), "first": bars[0]["t"][:10] if bars else None,
                     "last": last, "last_within_7d_of_delisting": near})
    reuse_rows = []
    for sym in reused[:60]:
        old = next(r for r in delisted if r["symbol"] == sym)
        try:
            spliced = _bars_one(c, sym, "2016-01-01", (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat(), None)
            before = _bars_one(c, sym, max("2016-01-01", old.get("ipoDate") or "2016-01-01"), old["delistingDate"],
                               (datetime.fromisoformat(old["delistingDate"]) - timedelta(days=1)).date().isoformat())
        except RuntimeError as e:
            reuse_rows.append({"symbol": sym, "error": str(e)[:120]})
            continue
        g = _gaps(spliced)
        reuse_rows.append({"symbol": sym, "old_name": old.get("name"), "old_delisted": old["delistingDate"], "default_bars": len(spliced),
                           "default_first": spliced[0]["t"][:10] if spliced else None, "default_last": spliced[-1]["t"][:10] if spliced else None,
                           "gaps_over_40d": g[:3], "spliced_two_lives": bool(g) and bool(before) and any(a <= old["delistingDate"] <= b for a, b in g),
                           "asof_before_delisting_bars": len(before)})
    return {"delisted_since_2016_stocks": len(delisted), "reused_tickers": len(reused), "sample": len(picks),
            "sample_with_bars": ok, "sample_last_bar_within_7d": near_end, "sample_rows": rows,
            "reused_checked": len(reuse_rows), "reused_spliced": sum(1 for x in reuse_rows if x.get("spliced_two_lives")), "reused_rows": reuse_rows}


def secret_state() -> dict[str, str]:
    names = ["APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "ALPHAVANTAGE_API_KEY", "FINRA_API_KEY", "FINRA_API_SECRET", "POLYGON_API_KEY", "SEC_USER_AGENT", "FRED_API_KEY"]
    return {n: ("set" if os.environ.get(n) else "not set") for n in names}


def alpaca_bars(c: httpx.Client, symbols: list[str], start: str, end: str, asof: str | None = None) -> dict[str, Any]:
    kid, sec = os.environ.get("APCA_API_KEY_ID"), os.environ.get("APCA_API_SECRET_KEY")
    if not kid or not sec:
        return {"status": "BLOCKED", "reason": "APCA_API_KEY_ID / APCA_API_SECRET_KEY not set (free Alpaca account keys needed)"}
    headers = {"APCA-API-KEY-ID": kid, "APCA-API-SECRET-KEY": sec}
    params: dict[str, Any] = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start, "end": end, "feed": "sip", "adjustment": "raw", "limit": 10000}
    if asof:
        params["asof"] = asof
    per: dict[str, dict[str, Any]] = {s: {"bars": 0, "first": None, "last": None} for s in symbols}
    pages, token, http = 0, None, None
    while True:
        if token:
            params["page_token"] = token
        time.sleep(0.35)  # ≤ 200 requests/minute
        r = c.get(ALPACA, params=params, headers=headers, timeout=60)
        http = r.status_code
        pages += 1
        if r.status_code != 200:
            return {"status": "BLOCKED" if r.status_code in (401, 403) else "FAILED", "http": r.status_code, "message": r.text[:300], "pages": pages}
        body = r.json()
        for sym, bars in (body.get("bars") or {}).items():
            p = per.setdefault(sym, {"bars": 0, "first": None, "last": None})
            p["bars"] += len(bars)
            if bars:
                p["first"] = p["first"] or bars[0]["t"][:10]
                p["last"] = bars[-1]["t"][:10]
        token = body.get("next_page_token")
        if not token or pages >= 50:
            break
    return {"status": "OK", "http": http, "pages": pages, "per_symbol": per}


def alphavantage_listing(c: httpx.Client, day: str | None, state: str) -> dict[str, Any]:
    key = os.environ.get("ALPHAVANTAGE_API_KEY")
    if not key:
        return {"status": "BLOCKED", "reason": "ALPHAVANTAGE_API_KEY not set"}
    params = {"function": "LISTING_STATUS", "state": state, "apikey": key}
    if day:
        params["date"] = day
    r = c.get(AV, params=params, timeout=60)
    text = r.text
    if r.status_code != 200 or text.lstrip().startswith("{"):
        return {"status": "FAILED", "http": r.status_code, "message": text[:300].replace(key, "<key>")}  # an error in a 200
    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        return {"status": "FAILED", "http": 200, "message": "empty CSV"}
    ex, types = {}, {}
    for x in rows:
        ex[x.get("exchange")] = ex.get(x.get("exchange"), 0) + 1
        types[x.get("assetType")] = types.get(x.get("assetType"), 0) + 1
    return {"status": "OK", "http": 200, "header": list(rows[0].keys()), "rows": len(rows), "exchanges": ex, "asset_types": types,
            "ipo_range": [min(x.get("ipoDate") or "9" for x in rows), max(x.get("ipoDate") or "" for x in rows)],
            "delisting_range": [min((x.get("delistingDate") or "9") for x in rows), max((x.get("delistingDate") or "") for x in rows)],
            "sample": rows[:3]}


def nasdaq_directory(c: httpx.Client) -> dict[str, Any]:
    out = {}
    for name in ("nasdaqlisted.txt", "otherlisted.txt"):
        r = c.get(f"https://www.nasdaqtrader.com/dynamic/SymDir/{name}", timeout=60)
        lines = r.text.splitlines() if r.status_code == 200 else []
        out[name] = {"http": r.status_code, "rows": max(0, len(lines) - 2), "header": lines[0] if lines else None, "footer": lines[-1] if lines else None}
    return out


def finra_short_volume(c: httpx.Client) -> dict[str, Any]:
    """The first CNMS daily short-sale volume file available (documented start 2018-08-01) and a recent one."""
    out = {}
    for d in ("20180801", "20180802", "20190102", "20240102"):
        r = c.get(f"https://cdn.finra.org/equity/regsho/daily/CNMSshvol{d}.txt", timeout=60)
        lines = r.text.splitlines() if r.status_code == 200 else []
        out[d] = {"http": r.status_code, "rows": max(0, len(lines) - 1), "header": lines[0] if lines else None}
        time.sleep(1)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="free_probe.json")
    ap.add_argument("--only", default="", help="comma list: alpaca,av,av_delisted_all,nasdaq,finra (empty = all but av_delisted_all)")
    a = ap.parse_args()
    end = (datetime.now(timezone.utc) - timedelta(days=2)).date().isoformat()
    res: dict[str, Any] = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "secrets": secret_state()}
    only = {x for x in a.only.split(",") if x} or {"alpaca", "av", "nasdaq", "finra"}
    with httpx.Client() as c:
        if "alpaca" in only:
            res["alpaca_sip_raw_3"] = alpaca_bars(c, ["AAPL", "MSFT", "SPY"], "2016-01-01", end)
            if res["alpaca_sip_raw_3"].get("status") == "OK":
                res["alpaca_cases"] = alpaca_bars(c, [s for s, _ in CASES], "2016-01-01", end)
                res["alpaca_cases_notes"] = dict(CASES)
                res["alpaca_asof_fb_2020"] = alpaca_bars(c, ["FB", "META"], "2020-01-01", "2020-01-10", asof="2020-01-06")
                res["alpaca_asof_raw_ticker"] = alpaca_bars(c, ["FB", "META"], "2020-01-01", "2020-01-10", asof="-")
        if "av" in only:
            res["alphavantage_listing_2016_active"] = alphavantage_listing(c, "2016-01-04", "active")
            res["alphavantage_listing_2016_delisted"] = alphavantage_listing(c, "2016-01-04", "delisted")
        if "av_delisted_all" in only:  # the whole delisted list (no date): delisting dates as lifespans (instruction §3.C)
            res["alphavantage_listing_delisted_all"] = alphavantage_listing(c, None, "delisted")
        if "alpaca_delisted" in only:
            res["alpaca_delisted_and_reused"] = alpaca_delisted_and_reused(c)
        if "nasdaq" in only:
            res["nasdaq_symbol_directory"] = nasdaq_directory(c)
        if "finra" in only:
            res["finra_short_volume"] = finra_short_volume(c)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, indent=1, ensure_ascii=False)
    print(json.dumps(res, indent=1, ensure_ascii=False)[:60000])


if __name__ == "__main__":
    main()
