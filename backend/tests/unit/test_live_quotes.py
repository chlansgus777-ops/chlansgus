"""Live quotes (2026-09-28): ordering, duplicate prints, state words, subscriptions, reconnects — all offline (MOCK).
None of this is a live verification: the real stream is measured by scripts/quote_soak.py against Finnhub."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

from marketlens.application import live_quotes as lq
from marketlens.application.live_quotes import FinnhubStream, QuoteHub, display_state, Trade
from marketlens.domain.enums import TradingSession
from marketlens.domain.freshness import PlanCheck, recommendation_freshness

UTC = timezone.utc
# Wednesday 2026-09-30, 14:00 UTC = 10:00 ET (regular session)
REG = datetime(2026, 9, 30, 14, 0, tzinfo=UTC)
PRE = datetime(2026, 9, 30, 11, 0, tzinfo=UTC)      # 07:00 ET
CLOSED = datetime(2026, 10, 3, 15, 0, tzinfo=UTC)   # Saturday


def ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def hub(now: datetime, **kw) -> QuoteHub:
    h = QuoteHub(source="finnhub", max_symbols=kw.pop("max_symbols", 3), coverage_ko="test", now=lambda: now, **kw)
    h.connected = True
    return h


def test_older_trade_never_overwrites_newer():
    h = hub(REG)
    assert h.ingest_trade("AAPL", 101.0, ms(REG - timedelta(seconds=1)), 10)
    assert not h.ingest_trade("AAPL", 99.0, ms(REG - timedelta(seconds=5)), 10)  # late, older print
    t = h.latest("AAPL")
    assert t is not None and t.price == 101.0
    assert h.stats.out_of_order == 1


def test_same_price_newer_trade_updates_trade_time():
    h = hub(REG)
    h.ingest_trade("AAPL", 100.0, ms(REG - timedelta(seconds=10)), 5)
    v = h.version
    assert h.ingest_trade("AAPL", 100.0, ms(REG - timedelta(seconds=2)), 5)
    assert h.version > v  # clients are told: same price, newer trade
    row = h.rows(tickers=["AAPL"])[0]
    assert row["trade_time"] == (REG - timedelta(seconds=2)).isoformat()


def test_identical_print_counts_as_duplicate_but_refreshes_receipt():
    h = hub(REG)
    ts = ms(REG - timedelta(seconds=3))
    h.ingest_trade("AAPL", 100.0, ts, 5, received=REG - timedelta(seconds=2))
    v = h.version
    assert not h.ingest_trade("AAPL", 100.0, ts, 5, received=REG)
    st = h._states["AAPL"]
    assert st.duplicates == 1 and st.stream is not None and st.stream.received_ts == REG
    assert h.version > v  # the new receipt time is pushed at once, never surfaced later with an old-looking delay


def test_invalid_trades_are_ignored():
    h = hub(REG)
    assert not h.ingest_trade("AAPL", 0.0, ms(REG), 1)
    assert not h.ingest_trade("AAPL", -1.0, ms(REG), 1)
    assert not h.ingest_trade("AAPL", 10.0, 0, 1)
    assert h.latest("AAPL") is None


def test_stream_and_snapshot_are_never_merged():
    class Q:
        price, timestamp, source, volume, previous_close = 90.0, REG - timedelta(minutes=30), "finnhub", None, 88.0

    h = hub(REG)
    h.ingest_snapshot("AAPL", Q())
    h.ingest_trade("AAPL", 91.0, ms(REG - timedelta(seconds=1)), 1)
    row = h.rows(tickers=["AAPL"])[0]
    assert row["feed"] == "stream" and row["price"] == 91.0 and row["state"] == lq.LIVE
    st = h._states["AAPL"]
    assert st.snapshot is not None and st.snapshot.price == 90.0  # kept apart


def test_states_premarket_without_trade_keeps_last_price():
    """The pre-market 20-minute rule must not blank a name: yesterday's close is shown, marked as such."""
    h = hub(PRE)

    class Q:
        price, timestamp, source, volume, previous_close = 150.0, datetime(2026, 9, 29, 20, 0, tzinfo=UTC), "finnhub", None, 149.0

    h.ingest_snapshot("NVDA", Q())
    row = h.rows(tickers=["NVDA"])[0]
    assert row["price"] == 150.0 and row["state"] == lq.EXTENDED_NO_TRADE and row["session"] == "PREMARKET"


def test_states_closed_disconnected_delayed_quiet():
    t_old = Trade(100.0, REG - timedelta(minutes=10), REG - timedelta(minutes=10), 1, "finnhub", "stream")
    t_new = Trade(100.0, REG - timedelta(seconds=5), REG - timedelta(seconds=5), 1, "finnhub", "stream")
    snap = Trade(100.0, REG - timedelta(minutes=5), REG, None, "finnhub", "snapshot")
    assert display_state(t_new, REG, TradingSession.REGULAR, True, True, True, False) == lq.LIVE
    assert display_state(t_old, REG, TradingSession.REGULAR, True, True, True, False) == lq.QUIET
    assert display_state(t_new, REG, TradingSession.REGULAR, True, False, True, False) == lq.RECONNECTING
    assert display_state(snap, REG, TradingSession.REGULAR, True, True, True, False) == lq.DELAYED
    assert display_state(t_new, CLOSED, TradingSession.CLOSED, True, True, True, False) == lq.CLOSED_LAST
    assert display_state(None, REG, TradingSession.REGULAR, True, True, False, True) == lq.OVER_LIMIT
    assert display_state(None, REG, TradingSession.REGULAR, True, True, True, False) == lq.NO_DATA
    assert display_state(None, REG, TradingSession.REGULAR, False, False, True, False) == lq.UNAVAILABLE


def test_delayed_snapshot_is_never_labelled_live():
    snap = Trade(100.0, REG - timedelta(seconds=1), REG, None, "finnhub", "snapshot")
    assert display_state(snap, REG, TradingSession.REGULAR, True, True, True, False) != lq.LIVE
    assert display_state(snap, REG, TradingSession.REGULAR, False, False, True, False) != lq.LIVE


def test_subscription_plan_priority_and_limit():
    h = hub(REG, max_symbols=3)
    h.set_pinned("watchlist", ["W1", "W2"])
    h.set_pinned("holdings", ["H1", "H2"])
    h.view(["V1"])
    subscribed, over = h.plan()
    assert subscribed == ["V1", "H1", "H2"] and over == ["W1", "W2"]
    rows = {r["ticker"]: r for r in h.rows()}
    assert rows["W1"]["state"] == lq.OVER_LIMIT and not rows["W1"]["subscribed"]


def test_view_lease_expires():
    now = [REG]
    h = QuoteHub(source="finnhub", max_symbols=5, coverage_ko="", now=lambda: now[0])
    h.view(["AAPL"])
    assert h.plan()[0] == ["AAPL"]
    now[0] = REG + lq.VIEW_LEASE + timedelta(seconds=1)
    h._expire_views()
    assert h.plan()[0] == []


class FakeConn:
    def __init__(self, script: list[str | None | Exception]) -> None:
        self.script = list(script)
        self.sent: list[dict] = []
        self.closed = False

    def send(self, message: str) -> None:
        self.sent.append(json.loads(message))

    def recv(self, timeout: float) -> str | None:
        if not self.script:
            raise ConnectionError("server closed")
        x = self.script.pop(0)
        if isinstance(x, Exception):
            raise x
        return x

    def close(self) -> None:
        self.closed = True


def test_stream_subscribes_once_reconnects_and_resubscribes():
    """Two connections (the first drops): each subscribes the planned set exactly once; trades flow; no duplicates."""
    h = QuoteHub(source="finnhub", max_symbols=5, coverage_ko="")
    h.set_pinned("holdings", ["AAPL", "MSFT"])
    t0 = ms(datetime.now(tz=UTC))
    conns = [
        FakeConn([json.dumps({"type": "ping"}), json.dumps({"type": "trade", "data": [{"s": "AAPL", "p": 10.0, "t": t0, "v": 1}]})]),
        FakeConn([json.dumps({"type": "trade", "data": [{"s": "MSFT", "p": 20.0, "t": t0, "v": 1}, {"s": "XXX", "p": 1.0, "t": t0}]})]),
    ]
    made: list[FakeConn] = []
    done = threading.Event()

    def connect(url: str) -> FakeConn:
        assert url.startswith(lq.FINNHUB_WS)
        if not conns:
            done.set()
            raise ConnectionError("no more")
        c = conns.pop(0)
        made.append(c)
        return c

    s = FinnhubStream(h, "k", connect=connect, backoff_max=0.01)
    s.start()
    assert done.wait(10)
    s.stop()
    assert len(made) == 2 and all(c.closed for c in made)
    for c in made:
        subs = sorted(m["symbol"] for m in c.sent if m["type"] == "subscribe")
        assert subs == ["AAPL", "MSFT"]  # resubscribed after the reconnect, once each
    assert h.latest("AAPL").price == 10.0 and h.latest("MSFT").price == 20.0
    assert h.latest("XXX") is None  # unsubscribed symbols are ignored
    assert h.stats.connects == 2 and h.stats.disconnects == 2


def test_stream_error_message_triggers_reconnect_and_key_never_logged(caplog):
    h = QuoteHub(source="finnhub", max_symbols=5, coverage_ko="")
    h.set_pinned("holdings", ["AAPL"])
    calls = []
    done = threading.Event()

    def connect(url: str) -> FakeConn:
        calls.append(url)
        if len(calls) > 1:
            done.set()
            raise ConnectionError(f"refused {url}")
        return FakeConn([json.dumps({"type": "error", "msg": "Subscribing to too many symbols"})])

    s = FinnhubStream(h, "SECRETKEY123", connect=connect, backoff_max=0.01)
    s.start()
    assert done.wait(10)
    s.stop()
    assert "too many symbols" in (h.stats.last_error or "") or "refused" in (h.stats.last_error or "")
    assert "SECRETKEY123" not in (h.stats.last_error or "")
    assert "SECRETKEY123" not in caplog.text


def test_quote_does_not_start_analysis_or_write_rows():
    """Pricing and analysis are independent: ingesting ticks touches only the hub."""
    h = hub(REG)
    for i in range(1000):
        h.ingest_trade("AAPL", 100 + i * 0.01, ms(REG - timedelta(seconds=1000 - i)), 1)
    assert h.latest("AAPL").price == 100 + 999 * 0.01
    assert not hasattr(h, "service")  # the hub holds no reference to analysis, storage or scans


def test_revalidation_needs_a_new_price_not_the_analysis_price_again():
    """After a restart the cached analysis quote must not 're-validate' the recommendation."""
    as_of = REG - timedelta(minutes=45)
    price_ts = as_of - timedelta(seconds=30)
    plan = PlanCheck(100.0, 102.0, 95.0, 115.0, 2.0, True)
    same = recommendation_freshness(as_of, "FRESH", REG, plan=plan, quote_price=100.0, quote_ts=price_ts, analysis_price_ts=price_ts)
    assert same.status == "NEEDS_REVALIDATION"
    newer = recommendation_freshness(as_of, "FRESH", REG, plan=plan, quote_price=100.2, quote_ts=REG - timedelta(seconds=5), analysis_price_ts=price_ts)
    assert newer.status == "CURRENT" and newer.revalidated_price == 100.2


def test_setup_screen_accepts_only_a_local_model_url_and_plain_names():
    """AI 검토 with a free local model (settings tab): loopback URLs only, so the screen can never point the app
    (and the analysis it sends) at another host."""
    import pytest

    from marketlens.config import validate_setup

    ok = validate_setup({"OPENAI_BASE_URL": "http://127.0.0.1:11434/v1", "FAST_MODEL": "qwen2.5:7b", "MARKETLENS_SCHEDULER": "1", "LLM_PROVIDER": "openai_compatible"})
    assert ok["OPENAI_BASE_URL"] == "http://127.0.0.1:11434/v1" and ok["MARKETLENS_SCHEDULER"] == "1"
    for bad in ("https://api.example.com/v1", "http://127.0.0.1.evil.com/v1", "http://localhost@evil.com/v1", "http://10.0.0.5:11434/v1", "file:///etc/passwd"):
        with pytest.raises(ValueError):
            validate_setup({"OPENAI_BASE_URL": bad})
    for bad in ("qwen 7b", "a;rm -rf", "x" * 101):
        with pytest.raises(ValueError):
            validate_setup({"FAST_MODEL": bad})
    with pytest.raises(ValueError):
        validate_setup({"MARKETLENS_SCHEDULER": "yes"})


def test_hub_uses_the_service_clock():
    """The hub judges sessions with the same clock as the analysis (a fixed MOCK clock included)."""
    from tests.integration.test_service_api import make_service

    svc = make_service(universe=20)
    assert svc.quotes._now() == svc.now()


def test_latency_histogram_covers_the_whole_run_in_bounded_memory():
    from marketlens.application.live_quotes import Histogram

    h = Histogram()
    for i in range(100_000):
        h.add(float(i % 1000))  # 0..999 ms, uniform
    s = h.summary()
    assert s["n"] == 100_000 and 480 <= s["p50"] <= 520 and 940 <= s["p95"] <= 960 and s["max"] == 999.0
    assert len(h.counts) == Histogram.BUCKETS + 1  # never grows


def test_out_of_order_print_records_how_late_it_was():
    h = hub(REG)
    h.ingest_trade("AAPL", 101.0, ms(REG - timedelta(seconds=1)), 1)
    h.ingest_trade("AAPL", 100.0, ms(REG - timedelta(seconds=4)), 1)
    s = h.status()["out_of_order_late_by_ms"]
    assert s["n"] == 1 and 2990 <= s["max"] <= 3010


def test_closed_market_does_not_refetch_a_final_close():
    """Weekend: the Friday close is final — no REST refresh every few minutes."""
    from marketlens.domain.market_calendar import last_completed_session, session_close_utc

    now = [CLOSED]
    h = QuoteHub(source="finnhub", max_symbols=5, coverage_ko="", now=lambda: now[0], snapshot_every=timedelta(minutes=5))
    h.set_pinned("holdings", ["AAPL", "MSFT"])

    class Q:
        def __init__(self, ts):
            self.price, self.timestamp, self.source, self.volume, self.previous_close = 10.0, ts, "finnhub", None, None

    close = session_close_utc(last_completed_session(CLOSED))
    h.ingest_snapshot("AAPL", Q(close))                       # the final close
    h.ingest_snapshot("MSFT", Q(close - timedelta(hours=3)))  # an intraday value — not final
    now[0] = CLOSED + timedelta(hours=1)
    assert h._due_snapshots() == ["MSFT"]
