"""Alpaca historical daily bars (SIP, as traded) for the years before the Polygon free window: 2016 → Polygon's start.

Free Alpaca "Basic" market data: SIP history from 2016, 200 requests a minute. One key, one call every 0.35 s
(about 170 a minute); a 429 or 5xx waits a minute or more and is never retried faster.

Symbols are requested with ``asof=-``: the bars of the symbol exactly as it traded that day. With the default entity
mapping Alpaca returns META's bars from 2016 although the company traded as FB until 2022-06-09 (probe run
36370815109), which would put one company in the data twice. Literal-symbol bars are the same shape as Polygon's
grouped daily download, so the company behind a ticker on a day comes from the same ticker → CIK map (bt_ticker_map,
Polygon reference records) for every year, and a reused ticker's two companies are told apart by its listing
intervals. The defence rules of docs/freedata/FREE_DATA_FEASIBILITY.md are applied to every interval:

1. no unbounded request per company: a symbol's bars are split by the listing intervals of the map, and a bar
   outside every interval of its ticker is dropped (counted);
2. an interval with a gap of more than 40 days between two bars, or a close-to-close move beyond ±50 % within five
   sessions of a listing boundary that no recorded split explains, is UNRESOLVED (a boundary in the wrong place mixes
   two companies there); a move matching a common split ratio (±3 %) with no split recorded is UNRESOLVED too
   (unadjusted prices would turn it into a fake crash), and so is a recorded split the prices do not show —
   UNRESOLVED intervals are written to ``bt_unresolved`` and the
   backtest leaves them out;
3. listing ends are checked against Alpha Vantage's delisted list: a delisting it records for a requested ticker
   inside the window that the map has no boundary for (within 10 days) makes the interval containing it UNRESOLVED.
   Alpha Vantage's bulk dates (a day carrying more than 50 delistings) are not used as dates.

The first 30 days of the Polygon window are requested as well and compared with Polygon's closes (same symbol, same
day): the agreement rate goes into the coverage. Only days before Polygon's start are stored (source "alpaca").
Tickers whose best day never reached $2M of trading are not stored — the same rule as the Polygon years
(``collect.PRUNE_MAX_DV``: they can never pass the $20M 20-day average dollar volume rule).
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import time
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping, Sequence

import httpx
from sqlalchemy import insert, select
from sqlalchemy.engine import Engine

from marketlens.backtest.identity import TickerInterval
from marketlens.backtest.schema import bt_dividends, bt_engine, bt_ticker_map, bt_unresolved, file_sha256, get_meta, set_meta

log = logging.getLogger("marketlens.backtest.alpaca")

ALPACA_BARS = "https://data.alpaca.markets/v2/stocks/bars"
AV_QUERY = "https://www.alphavantage.co/query"
ALPACA_SPACING = 0.35  # seconds between calls: about 170 a minute < the free 200
ALPACA_START = date(2016, 1, 1)  # the free SIP history begins in 2016
BATCH = 40  # symbols per request (bars are paged 10,000 at a time across the batch)
PAGE_LIMIT = 10000
OVERLAP_DAYS = 30  # calendar days of the Polygon window requested for the cross-check
GAP_DAYS = 40
JUMP = 0.5  # |close / previous close − 1| beyond this near a boundary: two companies in one interval
BOUNDARY_SESSIONS = 5
SPLIT_TOL = 0.03
SPLIT_RATIOS = (1 / 2, 1 / 3, 1 / 4, 1 / 5, 1 / 10, 1 / 20, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 100.0)
AV_MATCH_DAYS = 10
AV_BULK = 50
CLOSE_MATCH = 0.005  # Alpaca vs Polygon close on the same day and symbol
PRUNE_MAX_DV = 2e6  # collect.PRUNE_MAX_DV
STOCK_TYPES = frozenset({"CS", "ADRC", "OS"})  # store.STOCK_TYPES: what the backtest universe can hold
BENCHMARKS = ("SPY",)


# ---------------------------------------------------------------------------------------------------- HTTP client
class Alpaca:
    """Paged daily bars for a batch of symbols. A symbol Alpaca rejects ("invalid symbol") is taken out of the batch
    and reported, never guessed at."""

    def __init__(self, key_id: str, secret: str, sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic,
                 transport: httpx.BaseTransport | None = None) -> None:
        self._h = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret}
        self._sleep, self._clock = sleep, clock
        self._last = -1e9
        self._c = httpx.Client(timeout=90, transport=transport)
        self.calls = 0

    def _get(self, params: Mapping[str, Any]) -> tuple[int, Any]:
        for attempt in range(6):
            wait = ALPACA_SPACING - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = self._clock()
            self.calls += 1
            try:
                r = self._c.get(ALPACA_BARS, params=dict(params), headers=self._h)
            except httpx.HTTPError:
                self._sleep(30 * (attempt + 1))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                self._sleep(60 * (attempt + 1))  # respect the limit: back off, never retry faster
                continue
            try:
                return r.status_code, r.json()
            except ValueError:
                return r.status_code, {"message": r.text[:300]}
        raise RuntimeError("Alpaca bars: gave up after 6 attempts")

    def bars(self, symbols: Sequence[str], start: date, end: date) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
        """({symbol: bars as traded}, {rejected symbol: message}) for [start, end]."""
        out: dict[str, list[dict[str, Any]]] = {}
        bad: dict[str, str] = {}
        todo = list(symbols)
        while todo:
            status, body = self._fetch(todo, start, end, out)
            if status == 200:
                return out, bad
            msg = str((body or {}).get("message") or body)[:200]
            if status in (400, 422):
                named = _invalid_symbols(msg, todo)
                if named:
                    for s in named:
                        bad[s] = msg
                    todo = [s for s in todo if s not in named]
                    continue
                if len(todo) == 1:
                    bad[todo[0]] = msg
                    return out, bad
                half = len(todo) // 2  # the message does not say which: split the batch until it does
                for part in (todo[:half], todo[half:]):
                    o, b = self.bars(part, start, end)
                    out.update(o)
                    bad.update(b)
                return out, bad
            raise RuntimeError(f"Alpaca bars: HTTP {status} {msg}")
        return out, bad

    def _fetch(self, symbols: Sequence[str], start: date, end: date, out: dict[str, list[dict[str, Any]]]) -> tuple[int, Any]:
        params: dict[str, Any] = {"symbols": ",".join(symbols), "timeframe": "1Day", "start": start.isoformat(), "end": end.isoformat(),
                                  "feed": "sip", "adjustment": "raw", "asof": "-", "limit": PAGE_LIMIT, "sort": "asc"}
        got: dict[str, list[dict[str, Any]]] = {}
        while True:
            status, body = self._get(params)
            if status != 200:
                return status, body
            for sym, bars in (body.get("bars") or {}).items():
                got.setdefault(sym, []).extend(bars or [])
            token = body.get("next_page_token")
            if not token:
                out.update(got)
                return 200, body
            params["page_token"] = token


def _invalid_symbols(msg: str, batch: Sequence[str]) -> list[str]:
    m = re.search(r"invalid symbols?\s*:?\s*(.+)", msg, re.I)
    if not m:
        return []
    named = {x.strip().strip('"\'').upper() for x in re.split(r"[,\s]+", m.group(1)) if x.strip()}
    return [s for s in batch if s.upper() in named]


# ---------------------------------------------------------------------------------------------------- pure checks
@dataclass(frozen=True, slots=True)
class Row:
    day: date
    o: float
    h: float
    l: float  # noqa: E741
    c: float
    v: float


def parse_bars(raw: Iterable[Mapping[str, Any]]) -> list[Row]:
    out = []
    for b in raw:
        try:
            d = datetime.fromisoformat(str(b["t"]).replace("Z", "+00:00")).date()
            out.append(Row(d, float(b["o"]), float(b["h"]), float(b["l"]), float(b["c"]), float(b.get("v") or 0.0)))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda r: r.day)
    return out


def interval_of(ivs: Sequence[TickerInterval], d: date) -> TickerInterval | None:
    for iv in ivs:
        if iv.valid_from <= d and (iv.valid_to is None or d < iv.valid_to):
            return iv
    return None


def split_by_interval(rows: Sequence[Row], ivs: Sequence[TickerInterval]) -> tuple[dict[date, list[Row]], int]:
    """Rows grouped by the listing interval (its valid_from) they fall in; rows outside every interval are dropped."""
    groups: dict[date, list[Row]] = {}
    dropped = 0
    for r in rows:
        iv = interval_of(ivs, r.day)
        if iv is None:
            dropped += 1
            continue
        groups.setdefault(iv.valid_from, []).append(r)
    return groups, dropped


def _split_factor(splits: Sequence[tuple[date, float, float]], prev: date, day: date) -> float:
    """Price factor of the splits executed in (prev, day]: a 4-for-1 (from 1 to 4) → 1/4 (raw prices divide by 4)."""
    f = 1.0
    for d, frm, to in splits:
        if prev < d <= day and frm > 0 and to > 0:
            f *= frm / to
    return f


def check_interval(rows: Sequence[Row], iv: TickerInterval, splits: Sequence[tuple[date, float, float]], window: tuple[date, date]) -> list[tuple[str, str]]:
    """Problems of one listing interval's bars: [(reason, detail)] — empty when the interval is usable."""
    out: list[tuple[str, str]] = []
    starts_inside = iv.valid_from > window[0]
    ends_inside = iv.valid_to is not None and iv.valid_to <= window[1]
    n = len(rows)
    for i in range(1, n):
        a, b = rows[i - 1], rows[i]
        gap = (b.day - a.day).days
        if gap > GAP_DAYS:
            out.append(("gap", f"{a.day}→{b.day} ({gap} days without a bar)"))
        if a.c <= 0 or b.c <= 0:
            continue
        ratio = b.c / a.c
        f = _split_factor(splits, a.day, b.day)
        adj = ratio / f  # the move with the recorded split taken out
        if f == 1.0 and any(abs(ratio / x - 1) <= SPLIT_TOL for x in SPLIT_RATIOS):
            out.append(("unrecorded_split", f"{a.day}→{b.day} close ×{ratio:.3f}, no split recorded"))
            continue
        if f != 1.0 and abs(adj - 1) > JUMP:
            out.append(("split_mismatch", f"{a.day}→{b.day} close ×{ratio:.3f} but the recorded split gives ×{f:.3f}"))
            continue
        near = (starts_inside and i <= BOUNDARY_SESSIONS) or (ends_inside and i >= n - BOUNDARY_SESSIONS)
        if near and abs(adj - 1) > JUMP:
            out.append(("boundary_jump", f"{a.day}→{b.day} close ×{adj:.3f} within {BOUNDARY_SESSIONS} sessions of a listing boundary"))
    return out


def bulk_dates(rows: Iterable[Mapping[str, str]]) -> set[str]:
    c = Counter(r.get("delistingDate") or "" for r in rows)
    return {d for d, k in c.items() if d and k > AV_BULK}


def av_cross_check(av_rows: Sequence[Mapping[str, str]], ivs: Mapping[str, Sequence[TickerInterval]], window: tuple[date, date]) -> list[tuple[str, date, str, str]]:
    """[(ticker, interval valid_from, reason, detail)] for Alpha Vantage delistings the ticker map has no boundary for."""
    bulk = bulk_dates(av_rows)
    out: list[tuple[str, date, str, str]] = []
    for r in av_rows:
        t = (r.get("symbol") or "").replace("-", ".").upper()
        ds = r.get("delistingDate") or ""
        if t not in ivs or not ds or ds in bulk or (r.get("assetType") or "Stock") != "Stock":
            continue
        try:
            d = date.fromisoformat(ds)
        except ValueError:
            continue
        if not (window[0] <= d < window[1]):
            continue
        ends = [iv.valid_to for iv in ivs[t] if iv.valid_to is not None]
        if any(abs((e - d).days) <= AV_MATCH_DAYS for e in ends):
            continue
        iv = interval_of(ivs[t], d)
        if iv is not None:
            out.append((t, iv.valid_from, "av_delisting_not_in_map", f"Alpha Vantage: {t} delisted {ds} ({r.get('name') or ''}); the map has no listing end within {AV_MATCH_DAYS} days"))
    return out


# ---------------------------------------------------------------------------------------------------- collection
def _intervals(eng: Engine) -> dict[str, list[TickerInterval]]:
    out: dict[str, list[TickerInterval]] = {}
    with eng.connect() as c:
        for r in c.execute(select(bt_ticker_map)):
            out.setdefault(r.ticker, []).append(TickerInterval(r.ticker, r.valid_from, r.valid_to, r.cik, r.type, r.name, r.exchange))
    for v in out.values():
        v.sort(key=lambda i: i.valid_from)
    return out


def symbols_to_fetch(ivs: Mapping[str, Sequence[TickerInterval]], window: tuple[date, date]) -> list[str]:
    """Tickers with a stock listing interval overlapping the window, plus the benchmark."""
    keep = {t for t, lst in ivs.items() for iv in lst
            if (iv.type in STOCK_TYPES) and iv.valid_from < window[1] and (iv.valid_to is None or iv.valid_to > window[0])}
    return sorted(keep | set(BENCHMARKS))


def _splits(eng: Engine) -> dict[str, list[tuple[date, float, float]]]:
    from marketlens.infrastructure.db.models import CorporateActionRow as S

    out: dict[str, list[tuple[date, float, float]]] = {}
    seen: set[tuple[str, date]] = set()
    with eng.connect() as c:
        for t, d, frm, to in c.execute(select(S.ticker, S.execution_date, S.split_from, S.split_to)):
            if (t, d) not in seen:  # one event per day whatever the number of sources
                seen.add((t, d))
                out.setdefault(t, []).append((d, float(frm), float(to)))
    for v in out.values():
        v.sort()
    return out


def _polygon_closes(eng: Engine, symbols: Sequence[str], start: date, end: date) -> dict[tuple[str, date], float]:
    from marketlens.infrastructure.db.models import PriceBarRow as P

    with eng.connect() as c:
        rows = c.execute(select(P.ticker, P.day, P.close).where(P.ticker.in_(list(symbols)), P.day >= start, P.day <= end, P.source == "polygon")).all()
    return {(t, d): float(px) for t, d, px in rows}


def collect_alpaca(eng: Engine, store: Any, alp: Alpaca, av_rows: Sequence[Mapping[str, str]], start: date, poly_start: date, deadline: float) -> dict[str, Any]:
    from marketlens.application.market_store import GROUPED_DAYS_KEY
    from marketlens.infrastructure.db.models import PriceBarRow as P

    window = (start, poly_start)
    ivs = _intervals(eng)
    splits = _splits(eng)
    syms = symbols_to_fetch(ivs, window)
    done: set[str] = set(json.loads(get_meta(eng, "alpaca.done") or "[]"))
    st: dict[str, Any] = json.loads(get_meta(eng, "alpaca.stats") or "{}") or {
        "symbols": 0, "with_bars": 0, "rows_stored": 0, "rows_outside_intervals": 0, "pruned_tickers": 0, "rejected": {},
        "overlap_pairs": 0, "overlap_match": 0, "overlap_mismatch_tickers": [], "unresolved": Counter()}
    st["unresolved"] = Counter(st.get("unresolved") or {})
    days: set[date] = {date.fromisoformat(x) for x in json.loads(get_meta(eng, "alpaca.days") or "[]")}
    if not get_meta(eng, "alpaca.av_checked"):
        rows = [(t, vf, reason, detail) for t, vf, reason, detail in av_cross_check(av_rows, ivs, window) if t in set(syms)]
        _unresolve(eng, rows)
        st["unresolved"].update(r[2] for r in rows)
        set_meta(eng, "alpaca.av_checked", {"av_rows": len(av_rows), "flagged": len(rows)})
    todo = [s for s in syms if s not in done]
    end = min(poly_start + timedelta(days=OVERLAP_DAYS), datetime.now(timezone.utc).date())
    for i in range(0, len(todo), BATCH):
        if time.monotonic() > deadline:
            log.warning("alpaca: deadline reached with %d symbols left (resume with another run)", len(todo) - i)
            break
        batch = todo[i:i + BATCH]
        got, bad = alp.bars(batch, start, end)
        st["rejected"].update({k: v[:120] for k, v in bad.items()})
        poly = _polygon_closes(eng, batch, poly_start, end)
        rows_out: list[dict[str, Any]] = []
        flagged: list[tuple[str, date, str, str]] = []
        now = datetime.now(timezone.utc)
        for sym in batch:
            rows = parse_bars(got.get(sym) or [])
            st["symbols"] += 1
            if not rows:
                continue
            st["with_bars"] += 1
            before = [r for r in rows if r.day < poly_start]
            mism = 0
            for r in rows:
                if r.day >= poly_start and (sym, r.day) in poly:
                    st["overlap_pairs"] += 1
                    ok = abs(r.c / poly[(sym, r.day)] - 1) <= CLOSE_MATCH if poly[(sym, r.day)] > 0 else False
                    st["overlap_match"] += ok
                    mism += not ok
            if mism and len(st["overlap_mismatch_tickers"]) < 200:
                st["overlap_mismatch_tickers"].append(sym)
            if not before:
                continue
            if sym not in BENCHMARKS and max(r.c * r.v for r in before) < PRUNE_MAX_DV:
                st["pruned_tickers"] += 1
                continue
            groups, dropped = split_by_interval(before, ivs.get(sym, []))
            st["rows_outside_intervals"] += dropped
            for vf, grp in groups.items():
                iv = next(x for x in ivs.get(sym, []) if x.valid_from == vf)
                for reason, detail in check_interval(grp, iv, splits.get(sym, []), window):
                    flagged.append((sym, vf, reason, detail))
                for r in grp:
                    rows_out.append({"ticker": sym, "day": r.day, "source": "alpaca", "open": r.o, "high": r.h, "low": r.l, "close": r.c, "volume": r.v, "retrieved_at": now})
                    days.add(r.day)
        with eng.begin() as c:
            for k in range(0, len(rows_out), 5000):
                c.execute(insert(P.__table__).prefix_with("OR IGNORE"), rows_out[k:k + 5000])
        _unresolve(eng, flagged)
        st["rows_stored"] += len(rows_out)
        st["unresolved"].update(Counter(reason for _t, _vf, reason in {(f[0], f[1], f[2]) for f in flagged}))
        done.update(batch)
        set_meta(eng, "alpaca.done", sorted(done))
        set_meta(eng, "alpaca.stats", {**st, "unresolved": dict(st["unresolved"])})
        set_meta(eng, "alpaca.days", sorted(d.isoformat() for d in days))
    if all(s in done for s in syms):
        loaded = store.grouped_days() | days
        store.set_setting(GROUPED_DAYS_KEY, ",".join(sorted(d.isoformat() for d in loaded)))
        set_meta(eng, "done.alpaca", {"start": start.isoformat(), "end": (poly_start - timedelta(days=1)).isoformat(), "symbols": len(syms), "sessions": len(days)})
    return {**st, "unresolved": dict(st["unresolved"]), "requests": alp.calls, "complete": all(s in done for s in syms), "symbols_total": len(syms)}


def _unresolve(eng: Engine, rows: Sequence[tuple[str, date, str, str]]) -> None:
    if not rows:
        return
    uniq = {(t, vf, reason): detail for t, vf, reason, detail in rows}
    with eng.begin() as c:
        c.execute(insert(bt_unresolved).prefix_with("OR IGNORE"), [{"ticker": t, "valid_from": vf, "reason": r, "detail": d[:500]} for (t, vf, r), d in uniq.items()])


# ---------------------------------------------------------------------------------------------------- earlier reference data
def av_delisted(key: str, sleep: Callable[[float], None] = time.sleep, transport: httpx.BaseTransport | None = None) -> list[dict[str, str]]:
    """Alpha Vantage LISTING_STATUS delisted (all dates): one call of the free key's 25 a day, spaced from any other
    call (a back-to-back call is answered '{}' with HTTP 200)."""
    with httpx.Client(timeout=90, transport=transport) as c:
        r = None
        for wait in (15, 65):
            sleep(wait)
            r = c.get(AV_QUERY, params={"function": "LISTING_STATUS", "state": "delisted", "apikey": key})
            if r.status_code == 200 and not r.text.lstrip().startswith("{"):
                return list(csv.DictReader(io.StringIO(r.text)))
    raise RuntimeError(f"Alpha Vantage LISTING_STATUS delisted: HTTP {r.status_code if r else '?'} {(r.text[:120] if r else '').replace(key, '<key>')}")


def collect_early_reference(eng: Engine, store: Any, poly: Any, start: date, poly_start: date) -> None:
    """Splits from a year before ``start`` and dividends from ``start`` up to what the Polygon collection already
    holds (its splits start four years before its bars, its dividends at its bars)."""
    from marketlens.domain.corporate_actions import SplitEvent

    if not get_meta(eng, "done.splits_early"):
        have = json.loads(get_meta(eng, "done.splits") or "{}").get("since")
        until = date.fromisoformat(have) if have else poly_start
        since = start - timedelta(days=365)
        ev = [SplitEvent(str(r["ticker"]).upper(), date.fromisoformat(r["execution_date"]), float(r["split_from"]), float(r["split_to"]), "polygon")
              for r in poly.pages("/v3/reference/splits", **{"execution_date.gte": since.isoformat(), "execution_date.lt": until.isoformat(),
                                                           "limit": 1000, "order": "asc", "sort": "execution_date"})
              if r.get("ticker") and r.get("execution_date") and r.get("split_from") and r.get("split_to")]
        store.save_splits(ev)
        set_meta(eng, "done.splits_early", {"events": len(ev), "since": since.isoformat(), "until": until.isoformat()})
    if not get_meta(eng, "done.dividends_early"):
        have = json.loads(get_meta(eng, "done.dividends") or "{}").get("since")
        until = date.fromisoformat(have) if have else poly_start
        rows, seq = [], {}
        for r in poly.pages("/v3/reference/dividends", **{"ex_dividend_date.gte": start.isoformat(), "ex_dividend_date.lt": until.isoformat(),
                                                         "limit": 1000, "order": "asc", "sort": "ex_dividend_date"}):
            t, ex = str(r.get("ticker") or "").upper(), r.get("ex_dividend_date")
            if not t or not ex or r.get("cash_amount") is None:
                continue
            exd = date.fromisoformat(str(ex)[:10])
            k = (t, exd)
            seq[k] = seq.get(k, 0) + 1
            rows.append({"ticker": t, "ex_date": exd, "seq": seq[k], "cash_amount": float(r["cash_amount"]), "currency": r.get("currency"),
                         "dividend_type": r.get("dividend_type"),
                         "declaration_date": date.fromisoformat(str(r["declaration_date"])[:10]) if r.get("declaration_date") else None,
                         "pay_date": date.fromisoformat(str(r["pay_date"])[:10]) if r.get("pay_date") else None})
        with eng.begin() as c:
            for i in range(0, len(rows), 5000):
                c.execute(insert(bt_dividends).prefix_with("OR IGNORE"), rows[i:i + 5000])
        set_meta(eng, "done.dividends_early", {"records": len(rows), "since": start.isoformat(), "until": until.isoformat()})


# ---------------------------------------------------------------------------------------------------- run
def run(db_path: str, max_minutes: float = 320, start: date = ALPACA_START) -> dict[str, Any]:
    """Extend a finished Polygon collection (collect.py) back to ``start``: early splits and dividends, Alpaca bars,
    then the SEC filings of the companies the earlier years add and the FRED records of the earlier weeks."""
    from marketlens.application.market_store import MarketStore
    from marketlens.backtest import collect as C
    from marketlens.infrastructure.db.session import make_session_factory
    from marketlens.providers.live.sec_edgar import SecEdgarProvider

    deadline = time.monotonic() + max_minutes * 60
    eng = bt_engine(db_path)
    if not get_meta(eng, "done.bars") or not get_meta(eng, "bars.start"):
        raise SystemExit("the Polygon collection (task=collect) must finish its bars first: resume it, then run this on its database")
    poly_start = date.fromisoformat(json.loads(get_meta(eng, "bars.start") or '""'))
    store = MarketStore(make_session_factory(eng), "LIVE")
    poly = C.Polygon(os.environ["POLYGON_API_KEY"])
    collect_early_reference(eng, store, poly, start, poly_start)
    av_rows: list[dict[str, str]] = []
    if not get_meta(eng, "alpaca.av_checked"):
        key = os.environ.get("ALPHAVANTAGE_API_KEY")
        if not key:
            raise SystemExit("ALPHAVANTAGE_API_KEY is needed once for the delisting cross-check")
        av_rows = [r for r in av_delisted(key) if (r.get("delistingDate") or "") >= start.isoformat()]
    alp = Alpaca(os.environ["APCA_API_KEY_ID"], os.environ["APCA_API_SECRET_KEY"])
    stats = collect_alpaca(eng, store, alp, av_rows, start, poly_start, deadline)
    if get_meta(eng, "done.alpaca"):
        sec = SecEdgarProvider(os.environ["SEC_USER_AGENT"])
        C.collect_sec(eng, store, sec, deadline)
        fred_key = os.environ.get("FRED_API_KEY")
        if fred_key:
            since = json.loads(get_meta(eng, "done.series") or "{}").get("since")
            if since and date.fromisoformat(since) > start - timedelta(days=400):
                with eng.begin() as c:
                    c.exec_driver_sql("DELETE FROM bt_meta WHERE key = 'done.series'")
            C.collect_series(eng, fred_key, start - timedelta(days=400))
            C.collect_fred(eng, fred_key, start, C.last_completed_session(datetime.now().astimezone()), deadline)
    cov = C.coverage(eng, store)
    with eng.connect() as c:
        unresolved = c.execute(select(bt_unresolved.c.reason)).all()
    cov["alpaca"] = stats
    cov["unresolved_intervals"] = dict(Counter(r[0] for r in unresolved))
    cov["meta_early"] = {k: json.loads(v) if (v := get_meta(eng, k)) else None for k in ("done.splits_early", "done.dividends_early", "done.alpaca", "alpaca.av_checked")}
    cov["complete"] = bool(get_meta(eng, "done.alpaca")) and bool(get_meta(eng, "done.sec")) and bool(get_meta(eng, "done.fred") or not os.environ.get("FRED_API_KEY"))
    cov["polygon_calls"] = poly.calls
    return cov


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-minutes", type=float, default=320)
    ap.add_argument("--start", default=ALPACA_START.isoformat())
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO)
    cov = run(a.db, a.max_minutes, date.fromisoformat(a.start))
    cov["db_sha256"] = file_sha256(a.db)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(cov, f, indent=1, default=str)
    print(json.dumps(cov, indent=1, default=str))


if __name__ == "__main__":
    main()
