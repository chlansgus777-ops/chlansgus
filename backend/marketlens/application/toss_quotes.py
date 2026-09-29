"""토스증권 prices as the app's real-time feed — every second, in every session Toss quotes (pre-market, regular,
after-hours and overnight), for up to 200 names per call (the spec's /api/v1/prices).

Owner 2026-09-29: "실시간 가격을 받고싶다 … 정규장에서만 실시간으로 보인다는거야?" — the free Finnhub feed prints only in the
regular session; outside it the screens showed the prior close and analyses had no current price.

- Uses the broker's own TossClient (one client id = one valid token: a second client would revoke the first).
- Asks for the hub's plan (viewed → holdings → watchlist → top candidates), US tickers only, one call per second;
  every 30 s when the market and overnight trading are both closed (weekends and holidays).
- A key / IP / account problem stops the feed (the connection card says why) and it tries again every 60 s; a rate
  limit or an outage backs off a few seconds. Nothing here is written to a log with a secret in it.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from marketlens.domain.enums import DataMode
from marketlens.domain.market import Quote
from marketlens.domain.market_calendar import classify_session, is_trading_day, to_ny
from marketlens.providers.live.toss import PRICES_MAX, TossError

log = logging.getLogger("marketlens.quotes.toss")
US_TICKER = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
EVERY_S = 1.0
IDLE_EVERY_S = 30.0
KEY_TROUBLE = ("NOT_CONFIGURED", "BAD_KEY", "IP_NOT_ALLOWED", "TOKEN_REVOKED")
SOURCE = "toss"


def quiet_now(now: datetime) -> bool:
    """No US price moves: a weekend or exchange holiday outside overnight trading hours."""
    ny = to_ny(now)
    return not is_trading_day(ny.date()) and not (ny.weekday() == 6 and ny.hour >= 20)  # Sunday 20:00 ET: overnight opens


class TossQuoteFeed:
    def __init__(self, hub: Any, broker: Any, now: Callable[[], datetime] | None = None, sleep: Callable[[float], None] | None = None) -> None:
        self.hub, self.broker = hub, broker
        self._now = now or (lambda: datetime.now(tz=timezone.utc))
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        self._thread: threading.Thread | None = None
        self.calls = 0
        self.prints = 0
        self.last_ok: datetime | None = None
        self.error: dict[str, Any] | None = None

    @property
    def active(self) -> bool:
        b = self.broker
        return bool(b.enabled and b.configured and b.client is not None)

    def status(self) -> dict[str, Any]:
        ok = self.last_ok is not None and (self._now() - self.last_ok).total_seconds() <= 10
        return {"active": self.active, "live": self.active and ok and self.error is None, "last_ok": self.last_ok.isoformat() if self.last_ok else None,
                "error": self.error, "capacity": PRICES_MAX, "every_s": EVERY_S, "calls": self.calls, "prints": self.prints}

    def symbols(self) -> list[str]:
        planned, _over = self.hub.plan()
        return [t for t in planned if US_TICKER.match(t)][:PRICES_MAX]

    def tick(self) -> float:
        """One round: ask, ingest; returns the seconds to wait before the next round."""
        if not self.active:
            self.hub.poll_capacity = 0
            return 5.0
        self.hub.poll_capacity = PRICES_MAX
        names = self.symbols()
        if not names:
            return EVERY_S
        try:
            self.calls += 1
            got = self.broker.client.prices(names)
        except TossError as e:
            self.error = {"kind": e.kind, "text": e.text, "at": self._now().isoformat()}
            if e.kind in KEY_TROUBLE:
                return 60.0
            return 5.0 if e.kind == "RATE_LIMITED" else 3.0
        except Exception as e:  # noqa: BLE001 - the feed never dies on one bad answer
            self.error = {"kind": "UNAVAILABLE", "text": f"토스 시세 오류: {type(e).__name__}", "at": self._now().isoformat()}
            return 3.0
        self.error = None
        self.last_ok = self._now()
        for sym, price, ts in got:
            if ts is None:
                continue  # a price without its time cannot be judged fresh — not shown as live
            if self.hub.ingest_poll(sym, float(price), ts, SOURCE):
                self.prints += 1
        return IDLE_EVERY_S if quiet_now(self._now()) else EVERY_S

    def fetch(self, names: list[str]) -> None:
        """Ask for these names now (an analysis of a name no screen shows yet); errors leave the provider's price."""
        want = [t for t in names if US_TICKER.match(t)][:PRICES_MAX]
        if not want or not self.active:
            return
        try:
            self.calls += 1
            got = self.broker.client.prices(want)
        except Exception as e:  # noqa: BLE001 - the analysis falls back to its providers
            log.info("toss price for analysis not available: %s", type(e).__name__)
            return
        for sym, price, ts in got:
            if ts is not None and self.hub.ingest_poll(sym, float(price), ts, SOURCE):
                self.prints += 1

    def run(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            wait = self.tick()
            self._sleep(max(0.05, wait - (time.monotonic() - t0)))

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self.run, daemon=True, name="toss-quotes")
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None


def live_quote(hub: Any, ticker: str, max_age_s: float = 90.0) -> Quote | None:
    """The newest Toss price of ``ticker`` as a Quote for an analysis, when the feed answered it recently — the same
    price the screens show, in every session Toss quotes. None otherwise (the analysis asks its providers)."""
    age = hub.poll_age(ticker)
    if age is None or age > max_age_s:
        return None
    st = hub._states.get(ticker.upper())
    tr = getattr(st, "poll", None)
    if tr is None:
        return None
    return Quote(ticker=ticker.upper(), price=tr.price, timestamp=tr.trade_ts, session=classify_session(tr.trade_ts), source=SOURCE, mode=DataMode.LIVE,
                 previous_close=getattr(st, "previous_close", None), is_realtime=True)
