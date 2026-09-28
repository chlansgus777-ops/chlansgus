"""Collect the point-in-time backtest data into a SEPARATE database (GitHub Actions: the sandbox cannot reach SEC).

Every step is resumable (bt_meta records what is done). Rate limits: Polygon free 5 requests/minute (one call every
12.5 s), SEC ≤ 10/second (the app's SEC client, 8/s), FRED 2/second (the app's FRED client). Secrets come from the
environment and are never written anywhere (recorded URLs drop credential parameters).

Data and their publication time (docs/backtest/DATA_COVERAGE.md):
- daily bars: Polygon grouped daily, adjusted=false (as traded); a session is public at 16:00 New York
- splits: Polygon reference; applied from the execution day 00:00 New York (app ShareBasis)
- dividends: Polygon reference (ex-date), for total return only
- SEC quarterly facts: the app's own parser (first-reported values + dated revisions); public at the filing date
- FRED/ALFRED: the app's own FRED client, recorded per weekly analysis time, replayed offline
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable

import httpx
from sqlalchemy import func, select
from sqlalchemy.engine import Engine

from marketlens.backtest.identity import TickerRecord, build_ticker_map, weekly_times
from marketlens.backtest.schema import (
    RecordingTransport, bt_dividends, bt_engine, bt_profiles, bt_series, bt_ticker_map, bt_tickers, file_sha256, get_meta, set_meta,
)
from marketlens.domain.corporate_actions import SplitEvent
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import is_trading_day, last_completed_session

log = logging.getLogger("marketlens.backtest.collect")
POLY = "https://api.polygon.io"
POLY_SPACING = 12.5  # seconds between Polygon calls: 4.8 / minute < the free 5 / minute
ADV_CANDIDATE = 15e6  # companies whose 20-day average dollar volume reached this at least once get SEC data
PRUNE_MAX_DV = 2e6  # a ticker whose best single day traded less than this can never reach the $20M ADV rule
REGIME_SERIES = ("VIXCLS", "DGS10", "DTB3", "DEXKOUS")
STOCK_TYPES = ("CS", "ADRC")


class Polygon:
    def __init__(self, key: str, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._key, self._sleep, self._clock = key, sleep, clock
        self._last = -1e9
        self._c = httpx.Client(timeout=90, transport=transport)
        self.calls = 0

    def get(self, url: str, **params: Any) -> tuple[int, Any]:
        for attempt in range(6):
            wait = POLY_SPACING - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            self.calls += 1
            try:
                r = self._c.get(url if url.startswith("http") else POLY + url, params={**params, "apiKey": self._key})
            except httpx.HTTPError:
                self._sleep(30 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                self._sleep(60 * (attempt + 1))  # respect the limit: back off, never retry faster
                continue
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, {}
        raise RuntimeError(f"Polygon {url.split('?')[0]}: gave up after 6 attempts")

    def pages(self, path: str, **params: Any) -> Any:
        status, body = self.get(path, **params)
        while True:
            if status != 200:
                raise RuntimeError(f"Polygon {path}: HTTP {status} {str(body)[:200]}")
            yield from body.get("results") or []
            nxt = body.get("next_url")
            if not nxt:
                return
            status, body = self.get(nxt)


def _d(s: Any) -> date | None:
    return date.fromisoformat(str(s)[:10]) if s else None


def collect_tickers(eng: Engine, poly: Polygon) -> None:
    if get_meta(eng, "done.tickers"):
        return
    rows, seq = [], {}
    for active in ("true", "false"):
        for r in poly.pages("/v3/reference/tickers", market="stocks", active=active, limit=1000, order="asc", sort="ticker"):
            t = str(r.get("ticker") or "").upper()
            if not t:
                continue
            seq[t] = seq.get(t, 0) + 1
            rows.append({"ticker": t, "seq": seq[t], "active": active == "true", "cik": int(r["cik"]) if str(r.get("cik") or "").isdigit() else None,
                         "type": r.get("type"), "name": (r.get("name") or "")[:256], "exchange": r.get("primary_exchange"), "delisted": _d(r.get("delisted_utc"))})
    with eng.begin() as c:
        c.execute(bt_tickers.delete())
        for i in range(0, len(rows), 5000):
            c.execute(bt_tickers.insert(), rows[i:i + 5000])
    set_meta(eng, "done.tickers", {"records": len(rows), "calls": poly.calls})


def build_map(eng: Engine, sec_current: dict[str, int]) -> None:
    with eng.connect() as c:
        recs = [TickerRecord(r.ticker, r.active, r.cik, r.type, r.name, r.exchange, r.delisted) for r in c.execute(select(bt_tickers))]
    ivs = build_ticker_map(recs, sec_current)
    with eng.begin() as c:
        c.execute(bt_ticker_map.delete())
        rows = [{"ticker": i.ticker, "valid_from": i.valid_from, "valid_to": i.valid_to, "cik": i.cik, "type": i.type, "name": i.name, "exchange": i.exchange} for i in ivs]
        for k in range(0, len(rows), 5000):
            c.execute(bt_ticker_map.insert(), rows[k:k + 5000])
    set_meta(eng, "done.map", {"intervals": len(ivs)})


def first_entitled_day(poly: Polygon, today: date) -> date:
    """The oldest session the key may read (the free plan: about two years back), by binary search over sessions
    (about seven calls): an entitled session answers 200 OK, an older one 403 NOT_AUTHORIZED."""
    days = [d for d in (today - timedelta(days=k) for k in range(560, 800)) if is_trading_day(d)]
    days.sort()
    lo, hi = 0, len(days) - 1  # invariant: days[hi] is entitled (checked below), the answer is in days[lo..hi]

    def ok(d: date) -> bool:
        st, body = poly.get(f"/v2/aggs/grouped/locale/us/market/stocks/{d.isoformat()}", adjusted="false")
        return st == 200 and body.get("status") in ("OK", "DELAYED")

    if not ok(days[hi]):
        raise RuntimeError(f"{days[hi]}: not entitled — the plan reads less than {(today - days[hi]).days} days back")
    if ok(days[lo]):
        return days[lo]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if ok(days[mid]):
            hi = mid
        else:
            lo = mid
    return days[hi]


def collect_bars(eng: Engine, store: Any, poly: Polygon, today: date, deadline: float) -> None:
    last = last_completed_session(datetime.now().astimezone())
    start_s = get_meta(eng, "bars.start")
    start = date.fromisoformat(json.loads(start_s)) if start_s else first_entitled_day(poly, today)
    set_meta(eng, "bars.start", start.isoformat())
    have = store.grouped_days()
    empty = set(json.loads(get_meta(eng, "bars.empty") or "[]"))
    d = start
    while d <= last:
        if time.monotonic() > deadline:
            log.warning("bars: deadline reached at %s (resume with another run)", d)
            return
        if is_trading_day(d) and d not in have and d.isoformat() not in empty:
            st, body = poly.get(f"/v2/aggs/grouped/locale/us/market/stocks/{d.isoformat()}", adjusted="false")
            if st != 200:
                raise RuntimeError(f"grouped {d}: HTTP {st} {str(body)[:200]}")
            bars = {}
            for r in body.get("results") or []:
                try:
                    bars[str(r["T"])] = Bar(d, float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"]))
                except (KeyError, TypeError, ValueError):
                    continue
            if bars:
                store.save_grouped(d, bars, "polygon")
            else:
                empty.add(d.isoformat())
                set_meta(eng, "bars.empty", sorted(empty))
        d += timedelta(days=1)
    set_meta(eng, "done.bars", {"start": start.isoformat(), "end": last.isoformat(), "sessions": len(store.grouped_days()), "empty": sorted(empty)})


def prune_bars(eng: Engine) -> dict[str, int]:
    """Drop tickers that never traded PRUNE_MAX_DV in one day (they can never pass the $20M 20-day average rule)."""
    from marketlens.infrastructure.db.models import PriceBarRow as P

    with eng.begin() as c:
        best = dict(c.execute(select(P.ticker, func.max(P.close * P.volume)).group_by(P.ticker)).all())
        drop = [t for t, v in best.items() if (v or 0) < PRUNE_MAX_DV and t != "SPY"]
        n = 0
        for i in range(0, len(drop), 500):
            n += c.execute(P.__table__.delete().where(P.ticker.in_(drop[i:i + 500]))).rowcount or 0
    out = {"tickers_dropped": len(drop), "rows_dropped": n, "tickers_kept": len(best) - len(drop)}
    set_meta(eng, "done.prune", out)
    return out


def collect_splits(eng: Engine, store: Any, poly: Polygon, since: date) -> None:
    if get_meta(eng, "done.splits"):
        return
    ev = [SplitEvent(str(r["ticker"]).upper(), date.fromisoformat(r["execution_date"]), float(r["split_from"]), float(r["split_to"]), "polygon")
          for r in poly.pages("/v3/reference/splits", **{"execution_date.gte": since.isoformat(), "limit": 1000, "order": "asc", "sort": "execution_date"})
          if r.get("ticker") and r.get("execution_date") and r.get("split_from") and r.get("split_to")]
    store.save_splits(ev)
    set_meta(eng, "done.splits", {"events": len(ev), "since": since.isoformat()})


def collect_dividends(eng: Engine, poly: Polygon, since: date) -> None:
    if get_meta(eng, "done.dividends"):
        return
    rows, seq = [], {}
    for r in poly.pages("/v3/reference/dividends", **{"ex_dividend_date.gte": since.isoformat(), "limit": 1000, "order": "asc", "sort": "ex_dividend_date"}):
        t, ex = str(r.get("ticker") or "").upper(), _d(r.get("ex_dividend_date"))
        if not t or ex is None or r.get("cash_amount") is None:
            continue
        k = (t, ex)
        seq[k] = seq.get(k, 0) + 1
        rows.append({"ticker": t, "ex_date": ex, "seq": seq[k], "cash_amount": float(r["cash_amount"]), "currency": r.get("currency"),
                     "dividend_type": r.get("dividend_type"), "declaration_date": _d(r.get("declaration_date")), "pay_date": _d(r.get("pay_date"))})
    with eng.begin() as c:
        c.execute(bt_dividends.delete())
        for i in range(0, len(rows), 5000):
            c.execute(bt_dividends.insert(), rows[i:i + 5000])
    set_meta(eng, "done.dividends", {"records": len(rows), "since": since.isoformat()})


def candidate_ciks(eng: Engine) -> dict[int, str]:
    """CIK → a ticker it traded under, for companies whose 20-day average dollar volume reached ADV_CANDIDATE."""
    from marketlens.infrastructure.db.models import PriceBarRow as P

    with eng.connect() as c:
        bars = c.execute(select(P.ticker, P.day, P.close, P.volume).order_by(P.ticker, P.day)).all()
        ivs: dict[str, list[Any]] = {}
        for r in c.execute(select(bt_ticker_map)):
            ivs.setdefault(r.ticker, []).append(r)
    out: dict[int, str] = {}
    cur, window = None, []  # type: ignore[var-annotated]
    for t, d, px, v in bars:
        if t != cur:
            cur, window = t, []
        window.append(px * v)
        if len(window) > 20:
            window.pop(0)
        if len(window) == 20 and sum(window) / 20 >= ADV_CANDIDATE:
            for iv in ivs.get(t, []):
                if iv.valid_from <= d and (iv.valid_to is None or d < iv.valid_to) and iv.cik and iv.type in STOCK_TYPES:
                    out.setdefault(int(iv.cik), t)
    return out


def collect_sec(eng: Engine, store: Any, sec: Any, deadline: float) -> None:
    done = set(json.loads(get_meta(eng, "sec.done") or "[]"))
    cands = candidate_ciks(eng)
    failed: dict[str, str] = json.loads(get_meta(eng, "sec.failed") or "{}")
    n = 0
    for cik, _t in sorted(cands.items()):
        if cik in done:
            continue
        if time.monotonic() > deadline:
            log.warning("sec: deadline reached (resume with another run)")
            break
        key = f"CIK{cik:010d}"
        sec._cik[key] = cik
        try:
            prof = sec.company_profile(key)
            with eng.begin() as c:
                c.execute(bt_profiles.delete().where(bt_profiles.c.cik == cik))
                c.execute(bt_profiles.insert().values(cik=cik, sector=prof.get("sector"), industry=prof.get("industry"), sic=prof.get("sic"),
                                                      payload=json.dumps({k: v for k, v in prof.items() if isinstance(v, (str, int, float, bool, type(None)))})))
            store.save_quarters(key, sec.get_quarterly(key))
        except Exception as e:  # noqa: BLE001 - recorded per company, reported in the coverage (never silently skipped)
            failed[str(cik)] = f"{type(e).__name__}: {str(e)[:160]}"
        done.add(cik)
        n += 1
        if n % 50 == 0:
            set_meta(eng, "sec.done", sorted(done))
            set_meta(eng, "sec.failed", failed)
    set_meta(eng, "sec.done", sorted(done))
    set_meta(eng, "sec.failed", failed)
    if all(c in done for c in cands):
        set_meta(eng, "done.sec", {"candidates": len(cands), "failed": len(failed)})


def collect_fred(eng: Engine, fred_key: str, start: date, end: date, deadline: float) -> None:
    """Record the app's own FRED requests for every weekly analysis time (ALFRED vintages), to replay offline."""
    from marketlens.domain.macro import ALL_SERIES
    from marketlens.providers.live.fred import FredMacroProvider

    done = set(json.loads(get_meta(eng, "fred.done") or "[]"))
    prov = FredMacroProvider(fred_key, transport=RecordingTransport(eng))
    for t in weekly_times(start, end):
        if t.isoformat() in done:
            continue
        if time.monotonic() > deadline:
            break
        try:  # the answer (an error included) is recorded either way and replayed as the app would have seen it
            prov.get_series(list(ALL_SERIES), t)
        except Exception as e:  # noqa: BLE001 - recorded, reported in the coverage
            failed = json.loads(get_meta(eng, "fred.failed") or "{}")
            failed[t.isoformat()] = f"{type(e).__name__}: {str(e)[:160]}"
            set_meta(eng, "fred.failed", failed)
        done.add(t.isoformat())
        set_meta(eng, "fred.done", sorted(done))
    if len(done) >= len(weekly_times(start, end)):
        set_meta(eng, "done.fred", {"weeks": len(done)})


def collect_series(eng: Engine, fred_key: str, since: date) -> None:
    """Market series that are not revised (VIX, 10-year, 3-month bill, KRW) by observation day: regimes, cash yield."""
    if get_meta(eng, "done.series"):
        return
    with httpx.Client(timeout=60) as c, eng.begin() as db:
        for sid in REGIME_SERIES:
            time.sleep(0.6)
            r = c.get("https://api.stlouisfed.org/fred/series/observations",
                      params={"series_id": sid, "api_key": fred_key, "file_type": "json", "observation_start": since.isoformat()})
            r.raise_for_status()
            db.execute(bt_series.delete().where(bt_series.c.series_id == sid))
            rows = [{"series_id": sid, "day": date.fromisoformat(o["date"]), "value": float(o["value"])} for o in r.json().get("observations", [])
                    if o.get("value") not in (None, ".", "")]
            if rows:
                db.execute(bt_series.insert(), rows)
    set_meta(eng, "done.series", {"series": list(REGIME_SERIES), "since": since.isoformat()})


def coverage(eng: Engine, store: Any) -> dict[str, Any]:
    """Counts for DATA_COVERAGE.md: sessions, weeks, the ticker → CIK match rate (dollar-volume weighted)."""
    from marketlens.infrastructure.db.models import FundamentalVintageRow as F
    from marketlens.infrastructure.db.models import PriceBarRow as P

    days = sorted(store.grouped_days())
    with eng.connect() as c:
        ivs: dict[str, list[Any]] = {}
        for r in c.execute(select(bt_ticker_map)):
            ivs.setdefault(r.ticker, []).append(r)
        tot = {"all": 0.0, "stock": 0.0, "stock_mapped": 0.0, "stock_near_eligible": 0.0, "stock_near_eligible_mapped": 0.0}
        adv: dict[str, list[float]] = {}
        for t, d, px, v in c.execute(select(P.ticker, P.day, P.close, P.volume).order_by(P.ticker, P.day)):
            dv = px * v
            tot["all"] += dv
            iv = next((i for i in ivs.get(t, []) if i.valid_from <= d and (i.valid_to is None or d < i.valid_to)), None)
            if iv is None or iv.type not in STOCK_TYPES:
                continue
            w = adv.setdefault(t, [])
            w.append(dv)
            if len(w) > 20:
                w.pop(0)
            near = len(w) == 20 and sum(w) / 20 >= 20e6
            tot["stock"] += dv
            tot["stock_mapped"] += dv if iv.cik else 0.0
            if near:
                tot["stock_near_eligible"] += dv
                tot["stock_near_eligible_mapped"] += dv if iv.cik else 0.0
        companies = c.execute(select(func.count(func.distinct(F.ticker)))).scalar() or 0
        div = c.execute(select(func.count()).select_from(bt_dividends)).scalar() or 0
    return {
        "sessions": len(days), "first_session": days[0].isoformat() if days else None, "last_session": days[-1].isoformat() if days else None,
        "weeks": len({d.isocalendar()[:2] for d in days}),
        "cik_match_rate_stocks": round(tot["stock_mapped"] / tot["stock"], 4) if tot["stock"] else None,
        "cik_match_rate_near_eligible": round(tot["stock_near_eligible_mapped"] / tot["stock_near_eligible"], 4) if tot["stock_near_eligible"] else None,
        "companies_with_sec_quarters": companies, "dividend_records": div,
        "meta": {k: json.loads(v) if (v := get_meta(eng, k)) and v[:1] in "[{" else v for k in
                 ("done.tickers", "done.map", "done.bars", "done.prune", "done.splits", "done.dividends", "done.sec", "done.fred", "done.series")},
        "sec_failed": len(json.loads(get_meta(eng, "sec.failed") or "{}")),
    }


def run(db_path: str, today: date, max_minutes: float = 330) -> dict[str, Any]:
    from marketlens.application.market_store import MarketStore
    from marketlens.infrastructure.db.session import make_session_factory
    from marketlens.providers.live.sec_edgar import SecEdgarProvider

    deadline = time.monotonic() + max_minutes * 60
    eng = bt_engine(db_path)
    store = MarketStore(make_session_factory(eng), "LIVE")
    poly = Polygon(os.environ["POLYGON_API_KEY"])
    sec = SecEdgarProvider(os.environ["SEC_USER_AGENT"])
    sec_current = {s.ticker: int(s.cik) for s in sec.list_securities() if s.cik}
    collect_tickers(eng, poly)
    if not get_meta(eng, "done.map"):
        build_map(eng, sec_current)
    collect_bars(eng, store, poly, today, deadline)
    start = date.fromisoformat(json.loads(get_meta(eng, "bars.start") or '""') or today.isoformat())
    if get_meta(eng, "done.bars") and not get_meta(eng, "done.prune"):
        prune_bars(eng)
    collect_splits(eng, store, poly, start - timedelta(days=4 * 365))
    collect_dividends(eng, poly, start)
    if get_meta(eng, "done.bars"):
        collect_sec(eng, store, sec, deadline)
    fred_key = os.environ.get("FRED_API_KEY")
    if fred_key:
        collect_series(eng, fred_key, start - timedelta(days=400))
        collect_fred(eng, fred_key, start, last_completed_session(datetime.now().astimezone()), deadline)
    cov = coverage(eng, store)
    cov["complete"] = all(get_meta(eng, k) for k in ("done.tickers", "done.map", "done.bars", "done.prune", "done.splits", "done.dividends", "done.sec", "done.fred", "done.series"))
    cov["polygon_calls"] = poly.calls
    return cov


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-minutes", type=float, default=330)
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    cov = run(a.db, date.today(), a.max_minutes)
    cov["db_sha256"] = file_sha256(a.db)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(cov, f, indent=1)
    print(json.dumps(cov, indent=1))


if __name__ == "__main__":
    main()
