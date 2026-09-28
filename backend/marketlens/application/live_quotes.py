"""Live quotes: one app-wide latest-price state fed by the provider's official trade stream.

Independent of analysis (real-time quotes, 2026-09-28): a tick never starts an analysis, a financials fetch or a
scan, and never writes a database row. It only changes the in-memory latest price of a subscribed ticker and wakes
the clients' event stream.

What a row carries is kept apart on purpose:
- ``stream``   — the newest trade from the streaming feed (price, the provider's trade time, receipt time).
- ``snapshot`` — the newest REST quote (``/quote``) taken once when a ticker is subscribed, so a name shows its last
  price before its first trade arrives (market closed, pre-market without trades). It is never refreshed per second.
The two feeds are never merged into one number: the display picks the stream while it is live and says which
feed it shows.

Ordering: a trade older than the one held is dropped (counted as ``out_of_order``); a trade with the same time
and a newer receipt updates the receipt time only; a newer trade at the same price updates the trade time — the
price did not change, but the quote is newer.

Subscriptions are central: the desired set is the union of the watchlist, the holdings and the tickers being
viewed (each view holds a lease that expires unless renewed), capped at the feed's symbol limit — viewed names
first, then holdings, then the watchlist. Names over the limit are reported as such, never silently dropped.
"""

from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Protocol

from marketlens.domain.enums import TradingSession
from marketlens.domain.market_calendar import classify_session, last_completed_session, session_close_utc
from marketlens.infrastructure.logging import redact_text

log = logging.getLogger("marketlens.quotes")

# display states (the UI words live in the frontend; these are the contract)
LIVE = "LIVE"                      # 실시간 수신 중 — stream connected, a trade in the current session within LIVE_WINDOW
QUIET = "QUIET"                    # 연결됨·최근 체결 없음 — stream connected, the last trade is older (thin trading)
DELAYED = "DELAYED"                # 지연 시세 — shown from the REST snapshot, not the stream
CLOSED_LAST = "CLOSED_LAST"        # 장 마감·마지막 체결가
EXTENDED_NO_TRADE = "EXTENDED_NO_TRADE"  # 장전/시간외 체결 미수신 — only the prior session's last price is known
RECONNECTING = "RECONNECTING"      # 연결 끊김·재연결 중 (last price kept, with its own time)
NO_DATA = "NO_DATA"                # 아직 미수신 / 수신 실패
OVER_LIMIT = "OVER_LIMIT"          # 구독 한도 초과 — not streamed
UNAVAILABLE = "UNAVAILABLE"        # 스트림 미설정(키 없음·MOCK)

LIVE_WINDOW = timedelta(seconds=60)
VIEW_LEASE = timedelta(seconds=90)
PINNED_REFRESH = 30.0  # seconds: re-read the watchlist/holdings even without a change notice


class Connection(Protocol):
    def send(self, message: str) -> None: ...
    def recv(self, timeout: float) -> str | None: ...  # None on timeout
    def close(self) -> None: ...


@dataclass(slots=True)
class Trade:
    price: float
    trade_ts: datetime        # the provider's trade time
    received_ts: datetime     # when the backend received it
    volume: float | None
    source: str
    feed: str                 # "stream" or "snapshot" — never mixed


@dataclass(slots=True)
class TickerState:
    ticker: str
    stream: Trade | None = None
    snapshot: Trade | None = None
    previous_close: float | None = None
    version: int = 0
    trades: int = 0
    out_of_order: int = 0
    duplicates: int = 0
    snapshot_error: str | None = None
    snapshot_retry_at: datetime | None = None


@dataclass
class StreamStats:
    connects: int = 0
    disconnects: int = 0
    messages: int = 0
    trades: int = 0
    out_of_order: int = 0
    subscribe_msgs: int = 0
    unsubscribe_msgs: int = 0
    snapshot_calls: int = 0
    last_error: str | None = None
    connected_since: datetime | None = None
    last_message_at: datetime | None = None
    latencies_ms: list[float] = field(default_factory=list)  # provider trade time → backend receipt (last 2000)

    def note_latency(self, ms: float) -> None:
        self.latencies_ms.append(ms)
        if len(self.latencies_ms) > 2000:
            del self.latencies_ms[:1000]


def _pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 1)


class QuoteHub:
    """Thread-safe latest-quote state plus the central subscription set. The stream thread writes; the API reads."""

    def __init__(self, *, source: str, max_symbols: int, coverage_ko: str, now: Callable[[], datetime] | None = None,
                 snapshot: Callable[[str], Any] | None = None, streaming: bool = True, snapshot_source: str | None = None,
                 pinned_loader: Callable[[], dict[str, Iterable[str]]] | None = None,
                 snapshot_every: timedelta = timedelta(minutes=5), snapshot_gap: float = 1.1) -> None:
        self.source = source
        self.snapshot_source = snapshot_source or source
        self.max_symbols = max_symbols
        self.coverage_ko = coverage_ko
        self.streaming = streaming
        self.snapshot_every = snapshot_every
        self.snapshot_gap = snapshot_gap  # seconds between REST calls (the provider's free rate limit is shared)
        self._now = now or (lambda: datetime.now(tz=timezone.utc))
        self._snapshot = snapshot
        self._pinned_loader = pinned_loader
        self._last_pinned = 0.0
        self._stop = threading.Event()
        self._snap_thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._changed = threading.Condition(self._lock)
        self._states: dict[str, TickerState] = {}
        self._pinned: dict[str, set[str]] = {"holdings": set(), "watchlist": set()}
        self._views: dict[str, datetime] = {}
        self._version = 0
        self.connected = False
        self.stats = StreamStats()
        self._subs_changed = threading.Event()
        self._snap_queue: list[str] = []
        self._snap_event = threading.Event()

    # ------------------------------------------------------------------ subscriptions
    def set_pinned(self, kind: str, tickers: Iterable[str]) -> None:
        with self._lock:
            new = {t.upper() for t in tickers if t}
            if new == self._pinned.get(kind):
                return
            self._pinned[kind] = new
            self._touch(new)
        self._subs_changed.set()
        self._snap_event.set()

    def _touch(self, tickers: Iterable[str]) -> None:  # lock held: subscription changed → rows go out again
        for t in tickers:
            self._states.setdefault(t, TickerState(t))
        self._version += 1
        for st in self._states.values():
            st.version = self._version
        self._changed.notify_all()

    def view(self, tickers: Iterable[str]) -> None:
        """A screen shows these tickers: hold them for ``VIEW_LEASE`` (the client renews while the screen is open)."""
        until = self._now() + VIEW_LEASE
        added = False
        with self._lock:
            for t in tickers:
                t = t.upper()
                if not t:
                    continue
                added |= t not in self._views
                self._views[t] = until
            if added:
                self._touch(self._views)
        if added:
            self._subs_changed.set()
            self._snap_event.set()

    def _expire_views(self) -> bool:
        now = self._now()
        with self._lock:
            gone = [t for t, u in self._views.items() if u < now]
            for t in gone:
                del self._views[t]
        return bool(gone)

    def plan(self) -> tuple[list[str], list[str]]:
        """(subscribed, over the limit) — viewed first, then holdings, then the watchlist; stable order within each."""
        with self._lock:
            order: list[str] = []
            for group in (sorted(self._views), sorted(self._pinned["holdings"]), sorted(self._pinned["watchlist"])):
                for t in group:
                    if t not in order:
                        order.append(t)
        return order[: self.max_symbols], order[self.max_symbols:]

    # ------------------------------------------------------------------ ingest
    def ingest_trade(self, ticker: str, price: float, trade_ms: int, volume: float | None, received: datetime | None = None) -> bool:
        """One trade from the stream. Returns True when the held state changed."""
        if not (price > 0) or trade_ms <= 0:
            return False
        rec = received or self._now()
        ts = datetime.fromtimestamp(trade_ms / 1000, tz=timezone.utc)
        with self._lock:
            st = self._states.setdefault(ticker, TickerState(ticker))
            cur = st.stream
            self.stats.trades += 1
            if cur is not None and ts < cur.trade_ts:
                st.out_of_order += 1
                self.stats.out_of_order += 1
                return False
            if cur is not None and ts == cur.trade_ts and price == cur.price and volume == cur.volume:
                st.duplicates += 1  # identical print (same time, price, size) — keep the first, but it WAS received
                cur.received_ts = rec
                return False
            st.stream = Trade(price, ts, rec, volume, self.source, "stream")
            st.trades += 1
            self._bump(st)
        self.stats.note_latency((rec - ts).total_seconds() * 1000)
        return True

    def ingest_snapshot(self, ticker: str, quote: Any) -> None:
        price, ts = getattr(quote, "price", None), getattr(quote, "timestamp", None)
        if price is None or ts is None or price <= 0:
            return
        with self._lock:
            st = self._states.setdefault(ticker, TickerState(ticker))
            if st.snapshot is not None and ts <= st.snapshot.trade_ts:
                return
            st.snapshot = Trade(float(price), ts, self._now(), getattr(quote, "volume", None), getattr(quote, "source", None) or self.snapshot_source, "snapshot")
            st.previous_close = getattr(quote, "previous_close", None) or st.previous_close
            st.snapshot_error = None
            self._bump(st)

    def snapshot_failed(self, ticker: str, error: str) -> None:
        with self._lock:
            st = self._states.setdefault(ticker, TickerState(ticker))
            st.snapshot_error = redact_text(error)[:160]
            self._bump(st)

    def _bump(self, st: TickerState) -> None:  # lock held
        self._version += 1
        st.version = self._version
        self._changed.notify_all()

    def set_connected(self, ok: bool, error: str | None = None) -> None:
        with self._lock:
            if ok and not self.connected:
                self.stats.connects += 1
                self.stats.connected_since = self._now()
            if not ok and self.connected:
                self.stats.disconnects += 1
                self.stats.connected_since = None
            self.connected = ok
            if error:
                self.stats.last_error = redact_text(error)[:200]
            self._version += 1  # every row's state word may change
            for st in self._states.values():
                st.version = self._version
            self._changed.notify_all()

    # ------------------------------------------------------------------ read
    def wait(self, since: int, timeout: float) -> int:
        with self._lock:
            if self._version == since:
                self._changed.wait(timeout)
            return self._version

    @property
    def version(self) -> int:
        return self._version

    def latest(self, ticker: str) -> Trade | None:
        """The newest trade from either feed (for re-validating a stored recommendation against a newer price)."""
        with self._lock:
            st = self._states.get(ticker.upper())
            if st is None:
                return None
            c = [x for x in (st.stream, st.snapshot) if x is not None]
            return max(c, key=lambda x: x.trade_ts) if c else None

    def rows(self, since: int = 0, tickers: Iterable[str] | None = None) -> list[dict[str, Any]]:
        subscribed, over = self.plan()
        sub, ov = set(subscribed), set(over)
        want = {t.upper() for t in tickers} if tickers is not None else None
        now = self._now()
        with self._lock:
            names = set(self._states) | sub | ov
            out = []
            for t in sorted(names):
                if want is not None and t not in want:
                    continue
                st = self._states.get(t) or TickerState(t)
                if since and st.version <= since and want is None:
                    continue
                out.append(self._row(st, now, t in sub, t in ov))
        return out

    def _row(self, st: TickerState, now: datetime, subscribed: bool, over: bool) -> dict[str, Any]:
        session = classify_session(now)
        shown = st.stream
        if shown is None or (st.snapshot is not None and st.snapshot.trade_ts > shown.trade_ts):
            shown = st.snapshot if st.snapshot is not None else shown
        state = display_state(shown, now, session, self.streaming, self.connected, subscribed, over)
        base = {
            "ticker": st.ticker, "state": state, "session": session.value, "subscribed": subscribed, "version": st.version,
            "price": None, "trade_time": None, "received_time": None, "source": None, "feed": None,
            "previous_close": st.previous_close, "change_pct": None, "error": st.snapshot_error,
        }
        if shown is not None:
            base.update(price=shown.price, trade_time=shown.trade_ts.isoformat(), received_time=shown.received_ts.isoformat(),
                        source=shown.source, feed=shown.feed)
            if st.previous_close:
                base["change_pct"] = shown.price / st.previous_close - 1
        return base

    def status(self) -> dict[str, Any]:
        subscribed, over = self.plan()
        s = self.stats
        lat = list(s.latencies_ms)
        return {
            "source": self.source, "streaming": self.streaming, "connected": self.connected, "coverage": self.coverage_ko,
            "max_symbols": self.max_symbols, "subscribed": subscribed, "over_limit": over, "session": classify_session(self._now()).value,
            "connects": s.connects, "disconnects": s.disconnects, "messages": s.messages, "trades": s.trades, "out_of_order": s.out_of_order,
            "subscribe_msgs": s.subscribe_msgs, "unsubscribe_msgs": s.unsubscribe_msgs, "snapshot_calls": s.snapshot_calls,
            "last_error": s.last_error, "connected_since": s.connected_since.isoformat() if s.connected_since else None,
            "last_message_at": s.last_message_at.isoformat() if s.last_message_at else None,
            "provider_latency_ms": {"n": len(lat), "p50": _pct(lat, 0.5), "p95": _pct(lat, 0.95), "max": round(max(lat), 1) if lat else None},
        }

    # ------------------------------------------------------------------ pinned sets (watchlist, holdings)
    def refresh_pinned(self) -> None:
        """Re-read the watchlist and the holdings (after a change, and every ``PINNED_REFRESH`` from the loops)."""
        if self._pinned_loader is None:
            return
        try:
            got = self._pinned_loader()
        except Exception as e:  # noqa: BLE001 - keep the previous sets
            log.warning("quote subscriptions: pinned load failed: %s", type(e).__name__)
            return
        for kind, tickers in got.items():
            self.set_pinned(kind, tickers)
        self._last_pinned = time.monotonic()

    # ------------------------------------------------------------------ snapshots (REST, never per second)
    def request_snapshots(self, tickers: Iterable[str]) -> None:
        with self._lock:
            for t in tickers:
                if t not in self._snap_queue:
                    self._snap_queue.append(t)
        self._snap_event.set()

    def _due_snapshots(self) -> list[str]:
        """Planned names whose REST snapshot is missing or older than ``snapshot_every`` — and, while the stream is
        connected, only those without a stream trade in the current session (the stream is the source then)."""
        planned, _ = self.plan()
        now = self._now()
        seg = _session_start(now, classify_session(now))
        out = []
        with self._lock:
            for t in planned:
                st = self._states.get(t)
                if st is None or st.snapshot is None:
                    if st is None or st.snapshot_retry_at is None or st.snapshot_retry_at <= now:
                        out.append(t)
                    continue
                if self.streaming and self.connected and st.stream is not None and st.stream.trade_ts >= seg:
                    continue
                if now - st.snapshot.received_ts >= self.snapshot_every:
                    out.append(t)
        return out

    def snapshot_loop(self, stop: threading.Event) -> None:
        while not stop.is_set():
            self._snap_event.wait(5)
            self._snap_event.clear()
            if time.monotonic() - self._last_pinned >= PINNED_REFRESH:
                self.refresh_pinned()
            self._expire_views()
            self.request_snapshots(self._due_snapshots())
            while not stop.is_set():
                with self._lock:
                    t = self._snap_queue.pop(0) if self._snap_queue else None
                if t is None or self._snapshot is None:
                    break
                self.stats.snapshot_calls += 1
                try:
                    self.ingest_snapshot(t, self._snapshot(t))
                except Exception as e:  # noqa: BLE001 - one name failing never stops the others
                    self.snapshot_failed(t, f"{type(e).__name__}: {e}")
                with self._lock:  # a failed name waits for its next due time, not the next loop
                    st = self._states.setdefault(t, TickerState(t))
                    if st.snapshot is None:
                        st.snapshot_retry_at = self._now() + self.snapshot_every
                stop.wait(self.snapshot_gap)

    def start(self) -> None:
        """Start the snapshot thread (and nothing else — the stream, when there is one, starts its own)."""
        if self._snap_thread is not None:
            return
        self.refresh_pinned()
        self._snap_thread = threading.Thread(target=self.snapshot_loop, args=(self._stop,), daemon=True, name="quote-snapshots")
        self._snap_thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._snap_event.set()
        self._subs_changed.set()
        with self._lock:
            self._changed.notify_all()
        if self._snap_thread is not None:
            self._snap_thread.join(timeout=5)
            self._snap_thread = None


def display_state(shown: Trade | None, now: datetime, session: TradingSession, streaming: bool, connected: bool, subscribed: bool, over: bool) -> str:
    """The word for one row. The price itself is always the last valid one — a state never blanks it."""
    if shown is None:
        if over:
            return OVER_LIMIT
        if streaming and subscribed and not connected:
            return RECONNECTING
        return NO_DATA if streaming else UNAVAILABLE
    if session == TradingSession.CLOSED:
        return CLOSED_LAST
    in_session = shown.trade_ts >= _session_start(now, session)
    if shown.feed == "stream":
        if not connected:
            return RECONNECTING
        if in_session and now - shown.trade_ts <= LIVE_WINDOW:
            return LIVE
        if in_session:
            return QUIET
    if streaming and subscribed and not connected:
        return RECONNECTING
    if not in_session:
        # pre-market / after-hours / the open without a print yet: the last known price stays on screen, marked
        return EXTENDED_NO_TRADE
    if over:
        return OVER_LIMIT
    return DELAYED if shown.feed == "snapshot" else QUIET


def _session_start(now: datetime, session: TradingSession) -> datetime:
    """Start of the current session segment: 04:00 ET for PRE, 09:30 ET for REGULAR, the regular close for
    AFTER_HOURS. A trade before it belongs to an earlier segment (e.g. yesterday's close seen in the pre-market)."""
    from marketlens.domain.market_calendar import NY, PREMARKET_OPEN, REGULAR_OPEN, regular_close_time, to_ny

    ny = to_ny(now)
    if session == TradingSession.REGULAR:
        return datetime.combine(ny.date(), REGULAR_OPEN, tzinfo=NY).astimezone(timezone.utc)
    if session == TradingSession.AFTER_HOURS:
        return datetime.combine(ny.date(), regular_close_time(ny.date()), tzinfo=NY).astimezone(timezone.utc)
    if session == TradingSession.PREMARKET:
        return datetime.combine(ny.date(), PREMARKET_OPEN, tzinfo=NY).astimezone(timezone.utc)
    return session_close_utc(last_completed_session(now))


# ---------------------------------------------------------------------- Finnhub trade stream
FINNHUB_WS = "wss://ws.finnhub.io"


class FinnhubStream:
    """Finnhub's official WebSocket trade stream (``wss://ws.finnhub.io?token=…``; messages
    ``{"type":"trade","data":[{"s","p","t"(ms),"v","c"}]}`` and ``{"type":"ping"}``). One connection for the whole app;
    subscriptions are diffed against the hub's plan, never re-sent per screen. Reconnects with capped exponential
    backoff and jitter; resubscribes everything after a reconnect."""

    def __init__(self, hub: QuoteHub, api_key: str, connect: Callable[[str], Connection] | None = None,
                 idle_timeout: float = 45.0, backoff_max: float = 60.0) -> None:
        self.hub = hub
        self._key = api_key
        self._connect = connect or _websocket_connect
        self.idle_timeout = idle_timeout
        self.backoff_max = backoff_max
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self._subscribed: set[str] = set()

    def start(self) -> None:
        if self._threads:
            return
        th = threading.Thread(target=self._run, daemon=True, name="quote-stream")
        th.start()
        self._threads.append(th)

    def stop(self) -> None:
        self._stop.set()
        self.hub._subs_changed.set()
        for th in self._threads:
            th.join(timeout=5)
        self._threads.clear()

    def _run(self) -> None:
        delay = 1.0
        while not self._stop.is_set():
            conn: Connection | None = None
            try:
                conn = self._connect(f"{FINNHUB_WS}?token={self._key}")
                self.hub.set_connected(True)
                self._subscribed = set()
                self._sync_subs(conn)
                delay = 1.0
                self._loop(conn)
            except Exception as e:  # noqa: BLE001 - any failure → reconnect with backoff
                msg = self._scrub(f"{type(e).__name__}: {e}")  # the URL carries the key: never log or show it
                log.warning("quote stream: %s", msg)
                self.hub.set_connected(False, msg)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception as e:  # noqa: BLE001 - a dead socket may refuse to close; reconnect anyway
                        log.debug("quote stream close: %s", type(e).__name__)
                self.hub.set_connected(False)
            if self._stop.is_set():
                break
            self._stop.wait(delay * (0.5 + random.random() / 2))
            delay = min(self.backoff_max, delay * 2)

    def _scrub(self, text: str) -> str:
        return redact_text(text.replace(self._key, "***") if self._key else text)

    def _sync_subs(self, conn: Connection) -> None:
        self.hub._expire_views()
        if time.monotonic() - self.hub._last_pinned >= PINNED_REFRESH:
            self.hub.refresh_pinned()
        want, _over = self.hub.plan()
        wanted = set(want)
        for t in sorted(self._subscribed - wanted):
            conn.send(json.dumps({"type": "unsubscribe", "symbol": t}))
            self.hub.stats.unsubscribe_msgs += 1
        new = sorted(wanted - self._subscribed)
        for t in new:
            conn.send(json.dumps({"type": "subscribe", "symbol": t}))
            self.hub.stats.subscribe_msgs += 1
        self._subscribed = wanted
        if new:
            self.hub._snap_event.set()  # names without a snapshot get one (due check), not every name again

    def _loop(self, conn: Connection) -> None:
        last = time.monotonic()
        next_lease_check = time.monotonic() + 10
        while not self._stop.is_set():
            if self.hub._subs_changed.is_set() or time.monotonic() >= next_lease_check:
                self.hub._subs_changed.clear()
                next_lease_check = time.monotonic() + 10
                self._sync_subs(conn)
            raw = conn.recv(timeout=1.0)
            if raw is None:
                if time.monotonic() - last > self.idle_timeout:
                    raise TimeoutError(f"{self.idle_timeout:.0f}초 동안 메시지 없음(핑 포함) — 연결 재수립")
                continue
            last = time.monotonic()
            received = datetime.now(tz=timezone.utc)
            self.hub.stats.messages += 1
            self.hub.stats.last_message_at = received
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            kind = msg.get("type")
            if kind == "trade":
                for d in msg.get("data") or ():
                    try:
                        sym = str(d["s"]).upper()
                        if sym in self._subscribed:
                            self.hub.ingest_trade(sym, float(d["p"]), int(d["t"]), _f(d.get("v")), received)
                    except (KeyError, TypeError, ValueError):
                        continue
            elif kind == "error":
                raise ConnectionError(str(msg.get("msg", "stream error"))[:200])


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


class _WsConn:
    def __init__(self, ws: Any) -> None:
        self._ws = ws

    def send(self, message: str) -> None:
        self._ws.send(message)

    def recv(self, timeout: float) -> str | None:
        try:
            m = self._ws.recv(timeout=timeout)
        except TimeoutError:
            return None
        return m if isinstance(m, str) else m.decode("utf-8", "replace")

    def close(self) -> None:
        self._ws.close()


def _websocket_connect(url: str) -> Connection:
    from websockets.sync.client import connect  # optional dependency: only the live stream needs it

    return _WsConn(connect(url, open_timeout=15, close_timeout=5, ping_interval=20, ping_timeout=20, max_size=2**20))
