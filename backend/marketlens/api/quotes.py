"""Live-quote routes: the app-wide latest prices and their event stream.

- ``GET  /api/quotes``         — every held row (subscribed or seen) and the stream status.
- ``POST /api/quotes/view``    — the tickers a screen shows (a lease; the client renews it while the screen is open).
- ``GET  /api/quotes/stream``  — Server-Sent Events: ``quotes`` events carry only the rows that changed since the last
  event (coalesced over ``COALESCE_S``), ``status`` events the connection state; a comment line every 15 s keeps the
  connection alive. One stream per app window — the client keeps one shared store.

No route here analyses, fetches financials, scans or writes a database row.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from marketlens.api.routes import TICKER_RE, svc

router = APIRouter()
COALESCE_S = 0.05
HEARTBEAT_S = 15.0
STATUS_EVERY_S = 5.0


class ViewIn(BaseModel):
    tickers: list[str] = Field(default_factory=list, max_length=60)


@router.get("/quotes")
def quotes(req: Request) -> dict[str, Any]:
    s = svc(req)
    s.live_plans()
    hub = s.quotes
    return {"version": hub.version, "rows": hub.rows(), "status": hub.status()}


@router.get("/alerts")
def alerts(req: Request, after: int = 0) -> dict[str, Any]:
    """Zone changes seen on live prices (buy zone entered / left, stop or target hit, automatic re-analysis)."""
    j = svc(req).judge
    return {"alerts": j.alerts(after=after), "last_id": j.last_alert_id}


@router.post("/quotes/view")
def view(req: Request, body: ViewIn) -> dict[str, Any]:
    import re

    bad = [t for t in body.tickers if not re.match(TICKER_RE, t)]
    if bad:
        raise HTTPException(422, f"올바르지 않은 종목 코드: {bad[0][:12]}")
    hub = svc(req).quotes
    hub.view(body.tickers)
    subscribed, over = hub.plan()
    return {"subscribed": [t for t in (x.upper() for x in body.tickers) if t in subscribed],
            "over_limit": [t for t in (x.upper() for x in body.tickers) if t in over], "max_symbols": hub.max_symbols}


@router.post("/quotes/refresh-subscriptions")
def refresh_subscriptions(req: Request) -> dict[str, Any]:
    """Called by the UI after a watchlist or holdings change, so the subscription follows at once."""
    s = svc(req)
    hub = s.quotes
    hub.refresh_pinned()
    s.invalidate_live_plans()  # held / watched flags of the verdicts follow the change
    subscribed, over = hub.plan()
    return {"subscribed": subscribed, "over_limit": over}


def _sse(event: str, data: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, separators=(',', ':'), ensure_ascii=False)}\n\n".encode()


@router.get("/quotes/stream")
async def stream(req: Request) -> StreamingResponse:
    s = svc(req)
    hub = s.quotes
    judge = s.judge
    s.live_plans()

    async def gen() -> AsyncIterator[bytes]:
        v = hub.version
        first = hub.rows()
        sent_state = {r["ticker"]: r["state"] for r in first}
        last_alert = judge.last_alert_id
        last_app = await asyncio.to_thread(s.app_state)
        yield _sse("app", last_app)
        yield _sse("hello", {"version": v, "rows": first, "status": hub.status(), "alerts": judge.alerts(limit=20), "last_alert": last_alert})
        last_beat = last_status = time.monotonic()
        while True:
            if await req.is_disconnected():
                return
            nv = await asyncio.to_thread(hub.wait, v, 1.0)
            now = time.monotonic()
            if nv != v:
                await asyncio.sleep(COALESCE_S)  # let a burst of prints settle into one event
                nv = hub.version
                rows = hub.rows(since=v)
                v = nv
                if rows:
                    sent_state.update({r["ticker"]: r["state"] for r in rows})
                    yield _sse("quotes", {"version": v, "rows": rows, "sent_at": time.time()})
            if judge.last_alert_id != last_alert:
                new = judge.alerts(after=last_alert)
                last_alert = judge.last_alert_id
                if new:
                    yield _sse("alerts", {"alerts": new})
            if now - last_status >= STATUS_EVERY_S:
                last_status = now
                s.live_plans()  # background refresh when older than LIVE_PLAN_AGE (cheap when fresh)
                s.live_rejudge_tick()  # the list's top names re-judged on the live price (background, every few seconds)
                s.broker_tick()  # the 토스증권 account, on its own schedule (1 min in a session, 10 min otherwise)
                app = await asyncio.to_thread(s.app_state)
                if app != last_app:
                    last_app = app
                    yield _sse("app", app)
                # states that change with the clock alone (실시간 → 최근 체결 없음, the session bell)
                aged = [r for r in hub.rows() if sent_state.get(r["ticker"]) != r["state"]]
                if aged:
                    sent_state.update({r["ticker"]: r["state"] for r in aged})
                    yield _sse("quotes", {"version": v, "rows": aged, "sent_at": time.time()})
                yield _sse("status", hub.status())
            if now - last_beat >= HEARTBEAT_S:
                last_beat = now
                yield b": keep-alive\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
