"""LIVE soak test of the quote stream (real Finnhub trades — never MOCK).

Runs the production pieces end to end: FinnhubStream → QuoteHub → the /api/quotes/stream SSE route (served by
uvicorn on localhost) → an SSE client in this process. Measures, over ``--minutes``:

- provider latency: the provider's trade time → backend receipt (includes the exchange/Finnhub path and clock skew)
- display-path latency: backend receipt → SSE event parsed by the client (what the app adds)
- connection stability: connects, disconnects, messages, trades, out-of-order prints, subscribe/unsubscribe messages,
  REST snapshot calls; process RSS memory sampled every 30 s
- a forced network drop (the socket is closed from outside at ``--drop-at`` minutes) and the time to the next trade
- a subscription change mid-run (one ticker removed, one added)

Writes a JSON report. Requires FINNHUB_API_KEY in the environment; the key is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx


def rss_mb() -> float | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except OSError:
        return None
    return None


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(round(q * (len(s) - 1))))], 1)


def summary(xs: list[float]) -> dict[str, Any]:
    return {"n": len(xs), "p50": pct(xs, 0.5), "p95": pct(xs, 0.95), "p99": pct(xs, 0.99), "max": round(max(xs), 1) if xs else None,
            "mean": round(statistics.fmean(xs), 1) if xs else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=32)
    ap.add_argument("--tickers", default="AAPL,MSFT,NVDA,AMZN,TSLA,META,GOOGL,AMD,SPY,QQQ")
    ap.add_argument("--drop-at", type=float, default=10.0)
    ap.add_argument("--swap-at", type=float, default=15.0)
    ap.add_argument("--out", default="quote_soak.json")
    ap.add_argument("--port", type=int, default=8799)
    a = ap.parse_args()
    key = os.environ.get("FINNHUB_API_KEY")
    if not key:
        print("FINNHUB_API_KEY 없음 — LIVE 측정을 할 수 없습니다(MOCK으로 대체하지 않음).")
        return 2

    import uvicorn
    from fastapi import FastAPI

    from marketlens.api.quotes import router as quotes_router
    from marketlens.application.live_quotes import FinnhubStream, QuoteHub
    from marketlens.infrastructure.logging import configure_logging
    from marketlens.providers.live.finnhub import FinnhubProvider

    configure_logging("WARNING", [key], None)
    tickers = [t.strip().upper() for t in a.tickers.split(",") if t.strip()]
    rest = FinnhubProvider(key)
    pinned = {"holdings": tickers[:5], "watchlist": tickers[5:]}
    hub = QuoteHub(source="finnhub", max_symbols=50, coverage_ko="Finnhub trade stream", streaming=True,
                   snapshot=rest.get_quote, pinned_loader=lambda: pinned)

    conns: list[Any] = []

    def connect(url: str) -> Any:
        from marketlens.application.live_quotes import _websocket_connect

        c = _websocket_connect(url)
        conns.append(c)
        return c

    stream = FinnhubStream(hub, key, connect=connect)

    class Svc:
        quotes = hub

    app = FastAPI()
    app.state.service = Svc()
    app.include_router(quotes_router, prefix="/api")
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=a.port, log_level="warning"))
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)

    hub.start()
    stream.start()
    started = time.monotonic()
    t_start = datetime.now(tz=timezone.utc)
    display_lat: list[float] = []
    per_ticker: dict[str, int] = {}
    same_price_newer = 0
    last_seen: dict[str, tuple[float, str]] = {}
    events = 0
    sse_reconnects = 0
    mem: list[tuple[float, float | None]] = []
    drop_info: dict[str, Any] = {}
    swap_info: dict[str, Any] = {}
    stop = threading.Event()

    def sse_client() -> None:
        nonlocal events, same_price_newer, sse_reconnects
        while not stop.is_set():
            try:
                with httpx.stream("GET", f"http://127.0.0.1:{a.port}/api/quotes/stream", timeout=None) as r:
                    buf = ""
                    for chunk in r.iter_text():
                        if stop.is_set():
                            return
                        buf += chunk
                        while "\n\n" in buf:
                            block, buf = buf.split("\n\n", 1)
                            now = time.time()
                            ev, data = "message", []
                            for line in block.split("\n"):
                                if line.startswith("event:"):
                                    ev = line[6:].strip()
                                elif line.startswith("data:"):
                                    data.append(line[5:].strip())
                            if ev not in ("quotes", "hello") or not data:
                                continue
                            events += 1
                            for row in json.loads("\n".join(data)).get("rows", []):
                                if row.get("feed") != "stream" or not row.get("received_time"):
                                    continue
                                t = row["ticker"]
                                prev = last_seen.get(t)
                                key = (row["price"], row["trade_time"], row["received_time"])
                                if prev == key:
                                    continue  # a state-word change only (e.g. 실시간 → 최근 체결 없음): not a new trade
                                if ev == "quotes":  # the hello snapshot is history, not a delivery
                                    rec = datetime.fromisoformat(row["received_time"]).timestamp()
                                    display_lat.append((now - rec) * 1000)
                                per_ticker[t] = per_ticker.get(t, 0) + 1
                                if prev and prev[0] == row["price"] and prev[1] != row["trade_time"]:
                                    same_price_newer += 1
                                last_seen[t] = key
            except Exception:  # noqa: BLE001 - the soak keeps its client alive and counts reconnects
                sse_reconnects += 1
                time.sleep(1)

    threading.Thread(target=sse_client, daemon=True).start()
    try:
        while (el := time.monotonic() - started) < a.minutes * 60:
            if not mem or el - mem[-1][0] >= 30:
                mem.append((round(el), rss_mb()))
                print(f"[{el/60:5.1f}m] connected={hub.connected} trades={hub.stats.trades} events={events} rss={mem[-1][1]}MB", flush=True)
            if not drop_info and el >= a.drop_at * 60 and conns:
                trades_before = hub.stats.trades
                t0 = time.monotonic()
                try:
                    conns[-1].close()  # simulate a network drop: the socket dies under the stream
                except Exception:  # noqa: BLE001
                    pass
                drop_info = {"at_min": round(el / 60, 2), "connects_before": hub.stats.connects}
                while time.monotonic() - t0 < 120 and (hub.stats.connects <= drop_info["connects_before"] or hub.stats.trades <= trades_before):
                    time.sleep(0.05)
                drop_info.update(reconnected=hub.stats.connects > drop_info["connects_before"],
                                 seconds_to_reconnect_and_next_trade=round(time.monotonic() - t0, 2))
            if not swap_info and el >= a.swap_at * 60 and len(tickers) > 2:
                removed, added = pinned["watchlist"][-1] if pinned["watchlist"] else tickers[-1], "INTC"
                pinned["watchlist"] = [t for t in pinned["watchlist"] if t != removed] + [added]
                hub.refresh_pinned()
                time.sleep(3)
                swap_info = {"at_min": round(el / 60, 2), "removed": removed, "added": added, "subscribed_after": hub.plan()[0]}
            time.sleep(0.5)
    finally:
        stop.set()
        stream.stop()
        hub.stop()
        server.should_exit = True
    st = hub.status()
    report = {
        "kind": "LIVE",
        "source": "finnhub websocket trades (wss://ws.finnhub.io)",
        "started": t_start.isoformat(), "ended": datetime.now(tz=timezone.utc).isoformat(), "minutes": a.minutes,
        "sessions_seen": sorted({r["session"] for r in hub.rows()}),
        "tickers": tickers,
        "provider_latency_ms": hub.stats.latency.summary(),
        "out_of_order_late_by_ms": hub.stats.late_by.summary(),
        "display_path_latency_ms": summary(display_lat),
        "stream": {k: st[k] for k in ("connects", "disconnects", "messages", "trades", "out_of_order", "duplicates", "subscribe_msgs", "unsubscribe_msgs", "snapshot_calls", "last_error")},
        "sse_events": events, "sse_client_reconnects": sse_reconnects,
        "stream_rows_per_ticker": per_ticker,
        "same_price_newer_trade_updates": same_price_newer,
        "network_drop": drop_info, "subscription_swap": swap_info,
        "rss_mb": mem,
        "final_rows": [{k: r[k] for k in ("ticker", "state", "price", "trade_time", "feed", "session")} for r in hub.rows()],
    }
    Path(a.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print("QUOTE_SOAK_REPORT " + json.dumps(report, ensure_ascii=False))  # the whole report in the log (artifacts may be unreachable)
    return 0


if __name__ == "__main__":
    sys.exit(main())
