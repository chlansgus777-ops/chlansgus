"""The backtest's point-in-time market data, bound to one analysis time t at a time.

``BacktestData`` loads the separate backtest database once (bars as traded, splits, dividends, the ticker → CIK map,
the SEC fundamental vintages, the current SEC profiles). ``BacktestStore`` is what the app's own ``DataAccess`` reads
through (the same methods the operating ``MarketStore`` offers), answering only what was public at t:

- bars: sessions completed at t, split-adjusted with the splits executed on or before t (``share_multiplier`` — the
  app's one share-basis function); a later split never touches an earlier analysis;
- fundamentals: ``known_on`` the SEC filing visibility day of t (periods, fields and restatements filed later are
  removed);
- splits: executed on or before t; securities: listed (traded) at t, market cap = shares known at t × close at t;
- sector / industry / ETF flag: current values (the two documented exceptions of the time audit).

A security is one listing lineage: a ticker interval of the Polygon reference map, joined to the interval of the same
CIK under another ticker that starts trading within a week after it stopped (a rename). Two share classes of one CIK
trade at the same time and are never joined.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Mapping

from sqlalchemy import select, text
from sqlalchemy.engine import Engine

from marketlens.backtest.identity import TickerInterval
from marketlens.backtest.schema import bt_dividends, bt_profiles, bt_ticker_map, bt_unresolved
from marketlens.domain.corporate_actions import ShareBasis, SplitEvent, share_multiplier, split_factor
from marketlens.domain.enums import Exchange
from marketlens.domain.fundamentals import QuarterlyFinancials, known_on
from marketlens.domain.market import Bar, Security
from marketlens.domain.market_calendar import last_completed_session, to_ny

EXCHANGES = {"XNAS": Exchange.NASDAQ, "XNYS": Exchange.NYSE, "XASE": Exchange.NYSE_AMERICAN}  # the app's universe: the three US exchanges
STOCK_TYPES = frozenset({"CS", "ADRC", "OS"})
FUND_TYPES = frozenset({"ETF", "ETN", "ETV", "ETS", "FUND"})
RENAME_GAP_DAYS = 7
UNIVERSE_RECENT_DAYS = 10  # a security is in the universe at t when it traded within this many calendar days before t


@dataclass
class Lineage:
    key: str
    cik: int | None
    type: str | None
    name: str
    exchange: str | None
    days: list[date] = field(default_factory=list)
    rows: list[tuple[float, float, float, float, float]] = field(default_factory=list)  # o, h, l, c, v as traded
    labels: list[str] = field(default_factory=list)  # the ticker the bar traded under
    splits: list[SplitEvent] = field(default_factory=list)
    dividends: list[tuple[date, float]] = field(default_factory=list)


def _stream_bars(eng: Engine) -> Iterable[Any]:
    with eng.connect() as c:
        yield from c.execute(text("SELECT ticker, day, open, high, low, close, volume FROM price_bars ORDER BY day, ticker"))


def filing_visibility(ts: datetime) -> date:
    from marketlens.application.pipeline import filing_visibility_day

    return filing_visibility_day(ts)


class BacktestData:
    """Everything the backtest reads, loaded once from the backtest database (never the operating one)."""

    def __init__(self, eng: Engine, sf: Any) -> None:
        from marketlens.application.market_store import MarketStore

        self.eng = eng
        self._raw = MarketStore(sf, "LIVE")  # decoding of the stored fundamental vintages (the app's own code)
        with eng.connect() as c:
            ivs = [TickerInterval(r.ticker, r.valid_from, r.valid_to, r.cik, r.type, r.name, r.exchange) for r in c.execute(select(bt_ticker_map))]
            self.profiles = {r.cik: (r.sector or "Unknown", r.industry or "Unknown", json.loads(r.payload or "{}")) for r in c.execute(select(bt_profiles))}
            split_rows = c.execute(text("SELECT ticker, execution_date, split_from, split_to, source FROM corporate_actions")).all()
            div_rows = [(r.ticker, r.ex_date, r.cash_amount, r.currency) for r in c.execute(select(bt_dividends))]
            # listing intervals the collector could not resolve (alpaca.py defence rules): left out of the backtest
            self.unresolved = {(r.ticker, r.valid_from) for r in c.execute(select(bt_unresolved.c.ticker, bt_unresolved.c.valid_from))}
        self.unresolved_rows_skipped = 0
        self.intervals: dict[str, list[TickerInterval]] = {}
        for iv in ivs:
            self.intervals.setdefault(iv.ticker, []).append(iv)
        for lst in self.intervals.values():
            lst.sort(key=lambda i: i.valid_from)
        listings: dict[tuple[str, date | None], Lineage] = {}
        for tk, d, o, h, lo, cl, v in _stream_bars(eng):  # streamed: ten years of bars are never held twice in memory
            d = d if isinstance(d, date) else date.fromisoformat(str(d))
            iv = self.interval(tk, d)
            if iv is not None and (tk, iv.valid_from) in self.unresolved:
                self.unresolved_rows_skipped += 1
                continue
            lk = (tk, iv.valid_from if iv else None)
            ln = listings.get(lk)
            if ln is None:
                ln = listings[lk] = Lineage(f"{tk}@{iv.valid_from.isoformat() if iv else 'unmapped'}", iv.cik if iv else None, iv.type if iv else None,
                                            (iv.name if iv else None) or tk, iv.exchange if iv else None)
            if ln.days and ln.days[-1] == d:
                continue
            ln.days.append(d)
            ln.rows.append((float(o), float(h), float(lo), float(cl), float(v or 0.0)))
            ln.labels.append(tk)
        self.last_session = max((ln.days[-1] for ln in listings.values() if ln.days), default=None)
        self.first_session = min((ln.days[0] for ln in listings.values() if ln.days), default=None)
        self.lineages = self._join_renames(list(listings.values()))
        self.by_key = {ln.key: ln for ln in self.lineages}
        self._by_listing = {}
        for ln in self.lineages:
            for tk in set(ln.labels):
                self._by_listing.setdefault(tk, []).append(ln)
        for tk, d, sf_, st, src in split_rows:
            d = d if isinstance(d, date) else date.fromisoformat(str(d))
            ln = self.lineage_of(tk, d)
            if ln is not None and not any(s.execution_date == d for s in ln.splits):  # one event per day (two sources = one split)
                ln.splits.append(SplitEvent(ln.key, d, float(sf_), float(st), src))
        for tk, d, amt, cur in div_rows:
            ln = self.lineage_of(tk, d)
            if ln is not None and (cur or "USD") == "USD" and amt and amt > 0:
                ln.dividends.append((d, float(amt)))
        for ln in self.lineages:
            ln.splits.sort(key=lambda s: s.execution_date)
            ln.dividends.sort()
        self._quarters: dict[int, list[QuarterlyFinancials]] = {}

    # ------------------------------------------------------------------ identity
    def interval(self, ticker: str, d: date) -> TickerInterval | None:
        for iv in self.intervals.get(ticker, ()):
            if iv.valid_from <= d and (iv.valid_to is None or d < iv.valid_to):
                return iv
        return None

    def lineage_of(self, ticker: str, d: date) -> Lineage | None:
        """The security that traded (or was listed) as ``ticker`` on ``d``: its bar that day, else the nearest earlier one."""
        best: tuple[date, Lineage] | None = None
        for ln in self._by_listing.get(ticker, ()):
            i = bisect_right(ln.days, d) - 1
            while i >= 0 and ln.labels[i] != ticker:
                i -= 1
            if i >= 0 and (best is None or ln.days[i] > best[0]):
                best = (ln.days[i], ln)
        if best is not None:
            return best[1]
        # before the first stored bar (a split executed before the price window still re-bases older filings): the
        # security that first traded as ``ticker`` afterwards, if that is the same listing (not a later reuse)
        iv = self.interval(ticker, d)
        later = [(ln.days[ln.labels.index(ticker)], ln) for ln in self._by_listing.get(ticker, ())]
        if iv is None or not later:
            return None
        first_day, ln = min(later, key=lambda x: x[0])
        return ln if self.interval(ticker, first_day) == iv else None

    def benchmark(self, ticker: str = "SPY") -> Lineage | None:
        """The benchmark's history: the security that traded as ``ticker`` last. Not "the SPY without a CIK" — the
        real reference records give the SPDR trust its CIK (884394), and the first 7-year run (2026-10-03) found no
        benchmark that way: 0 weeks of data, no SPY return, §12/§13 never attempted."""
        return self.lineage_of(ticker, self.last_session) if self.last_session is not None else None

    def _join_renames(self, listings: list[Lineage]) -> list[Lineage]:
        by_cik: dict[int, list[Lineage]] = {}
        for ln in listings:
            if ln.cik is not None and ln.days:
                by_cik.setdefault(ln.cik, []).append(ln)
        absorbed: set[int] = set()
        for group in by_cik.values():
            group.sort(key=lambda x: x.days[0])
            for a in group:
                if id(a) in absorbed or self.last_session is None or (self.last_session - a.days[-1]).days <= RENAME_GAP_DAYS:
                    continue
                nxt = [b for b in group if b is not a and id(b) not in absorbed and b.days[0] > a.days[-1]
                       and (b.days[0] - a.days[-1]).days <= RENAME_GAP_DAYS]
                if len(nxt) != 1:
                    continue  # none, or ambiguous (never guessed)
                b = nxt[0]
                b.days[:0], b.rows[:0], b.labels[:0] = a.days, a.rows, a.labels  # the successor carries the history
                b.key = a.key
                absorbed.add(id(a))
        return [ln for ln in listings if id(ln) not in absorbed]

    # ------------------------------------------------------------------ fundamentals
    def quarters(self, cik: int) -> list[QuarterlyFinancials]:
        if cik not in self._quarters:
            self._quarters[cik] = self._raw.quarters(f"CIK{cik:010d}", None) or []
        return self._quarters[cik]


class BacktestStore:
    """The operating ``MarketStore`` interface as ``DataAccess`` uses it, answering as of one analysis time t."""

    def __init__(self, data: BacktestData) -> None:
        self.data = data
        self.t: datetime | None = None
        self._label: dict[str, Lineage] = {}
        self._bars: dict[str, list[Bar]] = {}
        self.reads: list[tuple[str, str]] = []  # (kind, label) — what the analysis read (time audit)

    # ------------------------------------------------------------------ time
    def set_time(self, t: datetime) -> None:
        self.t = t
        self._bars.clear()
        self.reads.clear()
        day = to_ny(t).date()
        self.session = last_completed_session(t)
        self.vis = filing_visibility(t)
        self._label = {}
        for ln in self.data.lineages:
            i = bisect_right(ln.days, self.session) - 1
            if i < 0 or (day - ln.days[i]).days > UNIVERSE_RECENT_DAYS:
                continue
            label = ln.labels[i]
            cur = self._label.get(label)
            if cur is None or ln.days[i] > cur.days[bisect_right(cur.days, self.session) - 1]:
                self._label[label] = ln

    def _need_time(self) -> datetime:
        if self.t is None:
            raise RuntimeError("BacktestStore: set_time() first")
        return self.t

    def lineage(self, label: str) -> Lineage | None:
        return self._label.get(label)

    def labels(self) -> dict[str, Lineage]:
        return dict(self._label)

    # ------------------------------------------------------------------ bars
    def _adjusted(self, ln: Lineage) -> list[Bar]:
        """All sessions completed at t, on the share basis of t (splits executed on or before t)."""
        hit = self._bars.get(ln.key)
        if hit is not None:
            return hit
        n = bisect_right(ln.days, self.session)
        splits = [s for s in ln.splits if s.execution_date <= self.session]
        out: list[Bar] = []
        for d, (o, h, lo, c, v) in zip(ln.days[:n], ln.rows[:n]):
            f = share_multiplier(splits, ShareBasis(d), self.session) or 1.0 if splits else 1.0
            out.append(Bar(d, o / f, h / f, lo / f, c / f, v * f))
        self._bars[ln.key] = out
        return out

    def bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        self._need_time()
        ln = self._label.get(ticker)
        self.reads.append(("bars", ticker))
        if ln is None:
            return []
        return [b for b in self._adjusted(ln) if start <= b.day <= end]

    def last_bars_all(self, start: date, end: date) -> dict[str, list[Bar]]:
        self._need_time()
        out = {}
        for label, ln in self._label.items():
            bs = [b for b in self._adjusted(ln) if start <= b.day <= end]
            if bs:
                out[label] = bs
        return out

    # ------------------------------------------------------------------ universe
    def securities(self, as_of: date | None = None) -> list[Security]:
        self._need_time()
        out: list[Security] = []
        for label, ln in sorted(self._label.items()):
            exch = EXCHANGES.get(ln.exchange or "")
            if exch is None or (ln.type not in STOCK_TYPES and ln.type not in FUND_TYPES):
                continue  # outside the app's universe (OTC, other venues, units, rights, warrants, preferreds)
            sector, industry, prof = self.data.profiles.get(ln.cik or -1, ("Unknown", "Unknown", {}))
            bars = self._adjusted(ln)
            out.append(Security(
                ticker=label, company_name=ln.name, exchange=exch, sector=sector, industry=industry,
                market_cap=self.market_cap(ln, bars[-1].close if bars else None), is_etf=ln.type in FUND_TYPES,
                is_adr=ln.type == "ADRC" or bool(prof.get("foreign_issuer")), country_of_incorporation=str(prof.get("country") or "US")[:8],
                currency="USD", active=True, listed_at=ln.days[0] if ln.days and ln.days[0] > (self.data.first_session or ln.days[0]) else None,
                delisted_at=None, cik=ln.cik))
        return out

    def market_cap(self, ln: Lineage, close: float | None) -> float | None:
        """Shares outstanding known at t (SEC filings visible at t, on the share basis of t) × the close at t."""
        if close is None or ln.cik is None:
            return None
        best: tuple[date, date, float] | None = None
        for q in known_on(self.data.quarters(ln.cik), self.vis):
            if q.shares_outstanding:
                fd = q.field_filed.get("shares_outstanding", q.filed_date)
                if best is None or (q.period_end, fd) > (best[0], best[1]):
                    best = (q.period_end, fd, q.shares_outstanding)
        if best is None:
            return None
        return close * best[2] * split_factor([s for s in ln.splits if s.execution_date <= self.session], best[1], self.session)

    def is_active(self, ticker: str) -> bool | None:
        return ticker in self._label

    # ------------------------------------------------------------------ fundamentals / corporate actions
    def quarters(self, ticker: str, max_age: timedelta | None) -> list[QuarterlyFinancials] | None:
        self._need_time()
        ln = self._label.get(ticker)
        self.reads.append(("fundamentals", ticker))
        if ln is None or ln.cik is None:
            return None
        return known_on(self.data.quarters(ln.cik), self.vis) or None

    def splits(self, ticker: str) -> list[SplitEvent]:
        ln = self._label.get(ticker)
        if ln is None:
            return []
        return [SplitEvent(ticker, s.execution_date, s.split_from, s.split_to, s.source) for s in ln.splits if s.execution_date <= self.session]

    # ------------------------------------------------------------------ everything else the store answers: nothing
    def get_setting(self, key: str) -> str | None:
        return "1" if key == "bars_backfill_complete" else None  # the backtest store is complete by construction

    def ingestion(self, dataset: str, ticker: str) -> None:
        return None

    def record_ingestion(self, *a: Any, **k: Any) -> None:
        return None

    def estimate_history(self, ticker: str, until: date) -> list[Any]:
        return []  # consensus history as known then is not available (unverifiable)

    def guidance(self, ticker: str, until: datetime) -> list[Any]:
        return []

    def save_bars(self, *a: Any, **k: Any) -> int:
        raise RuntimeError("the backtest never writes provider data into its store")

    save_quarters = save_bars


def total_return_path(ln: Lineage, start: date, end: date) -> list[tuple[date, float, float]]:
    """(day, open, close) of the lineage from ``start`` to ``end`` as a total-return index: splits applied on their
    execution day, cash dividends reinvested at the close before the ex-date (price and dividend on one basis)."""
    i0 = bisect_right(ln.days, start - timedelta(days=1))
    i1 = bisect_right(ln.days, end)
    split_on = {s.execution_date: s.ratio for s in ln.splits}
    div_on: dict[date, float] = {}
    for d, a in ln.dividends:
        div_on[d] = div_on.get(d, 0.0) + a
    out: list[tuple[date, float, float]] = []
    idx = 1.0
    prev_close = None
    for k in range(i0, i1):
        d = ln.days[k]
        o, _h, _l, c, _v = ln.rows[k]
        if prev_close is None:
            base = o  # the index starts at the first session's open
            idx_open = 1.0
            idx = c / base
        else:
            r = split_on.get(d, 1.0)
            gross = (o * r + div_on.get(d, 0.0) * r) / prev_close if prev_close else 1.0
            idx_open = idx * gross
            idx = idx * (c * r + div_on.get(d, 0.0) * r) / prev_close
        out.append((d, idx_open, idx))
        prev_close = c
    return out


def lineage_map(ls: Iterable[Lineage]) -> Mapping[str, Lineage]:
    return {ln.key: ln for ln in ls}
