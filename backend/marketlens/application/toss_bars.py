"""토스증권 daily candles as the first source of the daily price history of the names that matter (owner 2026-10-04:
"토스api가 더 안정적이면 토스로 다 바꿔") — the held and watched names, the analysed pool, and every name a stock page or
a single analysis asks for. The market-wide download (one grouped request per session for the whole US market) stays on
Polygon: Toss has no market-wide candle call, and one call per name for ~10,000 names a day is not a free-tier load.
Fundamentals, estimates, macro, news and US indices are not in Toss's API at all and keep their own sources.

- Same basis as the stored bars: split-adjusted (``adjusted=true``), and a stored candle retrieved before a later split
  is rescaled by the store's split adjustment like any other row.
- Checked before it is kept: where the store already holds the same sessions from Polygon, Toss's closes must agree
  (median gap at most 0.05 %, 90 % of sessions within 0.5 %) and its volume must be the same market-wide volume (median
  ratio 0.8–1.25). A name that does not agree keeps its Polygon history, its earlier Toss rows are removed and the
  reason is shown (``status``) — the two are never mixed silently.
- Completed sessions only: a session's candle is used once its after-hours trading has ended (20:00 New York); the
  price of a session still trading is the quote feed's.
- Failures leave Polygon as the source: a name that failed is asked again after 30 minutes; a key / IP problem or a
  rate limit pauses every request (60 s for a rate limit, 30 minutes otherwise). Nothing secret is logged.
"""

from __future__ import annotations

import logging
import re
import threading
import time as _time
from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any, Callable

from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import is_trading_day, previous_trading_day, to_ny
from marketlens.providers.live.toss import CANDLES_MAX, TossError

log = logging.getLogger("marketlens.bars.toss")
SOURCE = "toss"  # == market_store.PREFERRED_BARS
US_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
HISTORY_DAYS = 420  # the scanner's history (scanner.HISTORY_CALENDAR_DAYS): 200-day line + a year
FULL_PAGES = 3  # 3 × 200 candles cover HISTORY_DAYS
INCREMENTAL_WITHIN = 150  # a name with Toss rows this recent only needs the newest page
AFTER_HOURS_END = time(20, 0)
RETRY_S = 1800.0
PAUSE_RATE_S = 60.0
PRICE_MEDIAN = 0.0005
PRICE_SESSION = 0.005
PRICE_SHARE = 0.9
VOLUME_RATIO = (0.8, 1.25)
MIN_OVERLAP = 5
EXTRA_PER_SESSION = 60  # names outside the background list (a stock page, a single analysis, a performance read) per session
STOP_KINDS = ("NOT_CONFIGURED", "BAD_KEY", "IP_NOT_ALLOWED", "TOKEN_REVOKED", "NO_ACCOUNT")


def complete_session(now: datetime) -> date:
    """The newest New York session whose after-hours trading has ended at ``now``."""
    ny = to_ny(now)
    d = ny.date()
    return d if is_trading_day(d) and ny.time() >= AFTER_HOURS_END else previous_trading_day(d)


def disagreement(toss: list[Bar], other: list[Bar]) -> str | None:
    """Why Toss's candles do not match the stored sessions of the other source (None: they agree, or fewer than
    ``MIN_OVERLAP`` shared sessions — nothing to contradict)."""
    mine = {b.day: b for b in other}
    pairs = [(b, mine[b.day]) for b in toss if b.day in mine and mine[b.day].close > 0]
    if len(pairs) < MIN_OVERLAP:
        return None
    gaps = [abs(t.close / o.close - 1) for t, o in pairs]
    if median(gaps) > PRICE_MEDIAN or sum(g <= PRICE_SESSION for g in gaps) < PRICE_SHARE * len(gaps):
        return f"종가 불일치(같은 날 {len(pairs)}거래일 중앙값 차이 {median(gaps):.2%})"
    ratios = [t.volume / o.volume for t, o in pairs if o.volume > 0]
    if ratios and not VOLUME_RATIO[0] <= median(ratios) <= VOLUME_RATIO[1]:
        return f"거래량 기준 다름(토스/기존 중앙값 {median(ratios):.2f}배)"
    return None


class TossBars:
    def __init__(self, broker: Any, store: Any, now: Callable[[], datetime], names: Callable[[], list[str]] | None = None,
                 clock: Callable[[], float] = _time.monotonic) -> None:
        self.broker, self.store, self._now, self.names, self._clock = broker, store, now, names, clock
        self._lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._done: dict[str, date] = {}  # ticker → the session it is current for (kept, or judged, that day)
        self._failed: dict[str, float] = {}  # ticker → clock time of the last failure
        self._pause_until = 0.0
        self.rejected: dict[str, str] = {}  # ticker → why its Toss candles are not used
        self.kept: set[str] = set()
        self.refreshed_for: date | None = None
        self.last_run: datetime | None = None
        self.error: dict[str, Any] | None = None
        self.calls = 0
        self._wanted: set[str] = set()  # the background list of the last round
        self._extra: tuple[date | None, int] = (None, 0)  # (session, names outside the list fetched in it)

    @property
    def active(self) -> bool:
        b = self.broker
        return bool(self.store is not None and b is not None and b.enabled and b.configured and b.client is not None)

    def status(self) -> dict[str, Any]:
        return {"active": self.active, "session": self.refreshed_for.isoformat() if self.refreshed_for else None, "kept": len(self.kept),
                "rejected": dict(sorted(self.rejected.items())[:50]), "error": self.error, "last_run": self.last_run.isoformat() if self.last_run else None}

    def _paused(self) -> bool:
        return self._clock() < self._pause_until

    def due(self) -> bool:
        return self.active and not self._paused() and self.names is not None and self.refreshed_for != complete_session(self._now())

    def ensure(self, ticker: str) -> bool:
        """Make ``ticker``'s stored history current from Toss (at most once per session; a no-op when it already is).
        True when its Toss candles are in the store."""
        t = ticker.upper()
        if not self.active or not US_TICKER.match(t) or self._paused():
            return False
        session = complete_session(self._now())
        if self._done.get(t) == session:
            return t in self.kept
        last = self._failed.get(t)
        if last is not None and self._clock() - last < RETRY_S:
            return False
        with self._lock:
            lk = self._locks.setdefault(t, threading.Lock())
        with lk:
            if self._done.get(t) == session:  # another thread did it while this one waited
                return t in self.kept
            if t not in self._wanted:  # a bounded number of other names a session: never one request per name of a long list
                with self._lock:
                    day, n = self._extra if self._extra[0] == session else (session, 0)
                    if n >= EXTRA_PER_SESSION:
                        return False
                    self._extra = (day, n + 1)
            return self._fetch(t, session)

    def _fetch(self, t: str, session: date) -> bool:
        last = self.store.last_bar_day(t, SOURCE)
        if last is not None and (session - last).days <= INCREMENTAL_WITHIN:
            since, count, pages = last - timedelta(days=14), min(CANDLES_MAX, (session - last).days + 20), 1
        else:
            since, count, pages = session - timedelta(days=HISTORY_DAYS), CANDLES_MAX, FULL_PAGES
        try:
            self.calls += 1
            got = self.broker.client.daily_candles(t, since, count=count, max_pages=pages)
        except TossError as e:
            if e.kind in STOP_KINDS or e.kind == "RATE_LIMITED":
                self._pause_until = self._clock() + (PAUSE_RATE_S if e.kind == "RATE_LIMITED" else RETRY_S)
                self.error = {"kind": e.kind, "text": e.text}
            elif e.status in (400, 404):
                self._done[t] = session  # Toss does not know this name: Polygon stays its source today
                self.rejected[t] = "토스에 없는 종목"
            else:
                self._failed[t] = self._clock()
            log.info("toss bars %s: %s", t, e.kind)
            return False
        bars = [b for b in got if b.day <= session]
        if not bars:
            self._failed[t] = self._clock()
            return False
        why = disagreement(bars, self.store.source_bars(t, bars[0].day, bars[-1].day, exclude=SOURCE))
        self._done[t] = session
        self._failed.pop(t, None)
        if why is not None:
            self.rejected[t] = why
            self.kept.discard(t)
            self.store.delete_bars(t, SOURCE)
            log.info("toss bars %s not used: %s", t, why)
            return False
        self.store.save_bars(t, bars, SOURCE)
        self.rejected.pop(t, None)
        self.kept.add(t)
        self.error = None
        return True

    def refresh(self) -> int:
        """The background round of one completed session: every name that matters, one after another (the client paces
        its own requests). Stops at a key / IP problem or a rate limit; the next round continues where it is due."""
        if not self.active or self.names is None:
            return 0
        session = complete_session(self._now())
        names = [x.upper() for x in self.names()]
        self._wanted = set(names)
        n = 0
        for t in names:
            if self._paused():
                break
            n += self.ensure(t)
        else:
            self.refreshed_for = session
        self.last_run = self._now()
        return n
