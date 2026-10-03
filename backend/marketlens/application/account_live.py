"""The account now — the numbers the 토스증권 app shows, every second (owner 2026-09-29: "토스랑 연동한 보유종목이랑
원화기준 수익이 안 맞아, 오늘의 브리핑의 내 계좌 수익도 안 맞고").

A holding from the connected account starts from Toss's own figures of its last sync (every minute in a session):
purchase amount, value, P&L and today's P&L — Toss counts today's P&L of a share bought today from its purchase price,
which a "price − previous close" sum gets wrong. Between two syncs only the price moves, so each figure moves by
quantity × (live price − the price Toss valued it at):
    value = value_toss + Δ,  P&L = P&L_toss + Δ,  today = today_toss + Δ,   Δ = Q·(P_live − P_toss).
A price older than the sync adds nothing (never an old price over a newer valuation).

A holding entered by hand or from the trade records has no broker figures: its value is at the live price (else the
last close), today's change the live price against the previous close; during a session without a live price today's
change is left out — never yesterday's move shown as today's.

In won, the Toss way (its API: "전체 자산을 현재 환율로 원화 환산한 기준"): the dollar figures at the current rate
(Toss's 매매기준율). The split of the won return into stock and currency at the purchase-time rates is fx_attribution.
"""

from __future__ import annotations

from datetime import datetime, timedelta
import math
from typing import Any

from marketlens.domain.enums import TradingSession
from marketlens.domain.market_calendar import classify_session, last_completed_session, previous_trading_day, session_close_utc, to_ny


def _f(v: Any) -> float | None:
    try:
        out = None if v is None else float(v)
        return out if out is not None and math.isfinite(out) else None
    except (TypeError, ValueError):
        return None


def _prev_close(svc: Any, ticker: str, q: Any) -> float | None:
    """Today's total change always compares against the previous trading day's close, including after-hours.
    A missing close is not replaced by an older one (which would turn it into a multi-day change)."""
    qday = to_ny(q.timestamp).date()
    want = previous_trading_day(qday)
    bars = svc.store.bars(ticker, want, want) if getattr(svc, "store", None) is not None else (svc.data.bars(ticker, want - timedelta(days=7), want).value or [])
    return next((b.close for b in bars if b.day == want and b.close), None)


def _close_move(svc: Any, ticker: str, day: Any) -> tuple[float | None, float | None, datetime | None]:
    """(close of ``day``, its change) from the stored closes — the last session's move when no session is open."""
    start = day - timedelta(days=14)
    bars = svc.store.bars(ticker, start, day) if getattr(svc, "store", None) is not None else (svc.data.bars(ticker, start, day).value or [])
    cl = [(b.day, b.close) for b in bars if b.day <= day and b.close]
    if len(cl) < 2 or cl[-1][0] != day:
        return (cl[-1][1] if cl else None), None, session_close_utc(cl[-1][0]) if cl else None
    move = cl[-1][1] - cl[-2][1] if cl[-2][0] == previous_trading_day(day) else None
    return cl[-1][1], move, session_close_utc(day)


def build(svc: Any, now: datetime) -> dict[str, Any]:
    snap = svc.broker.snapshot() if getattr(svc, "broker", None) is not None else None
    taken = datetime.fromisoformat(snap["taken_at"]) if snap else None
    st = svc.broker.status() if snap else {}
    toss_fresh = bool(snap) and not st.get("stale")
    fxd = (snap or {}).get("fx") or {}
    fx = _f(fxd.get("mid"))
    toss = {h["symbol"]: h for h in (snap or {}).get("holdings", []) if h.get("market") == "US" and h.get("currency") == "USD"}
    in_session = classify_session(now) in (TradingSession.PREMARKET, TradingSession.REGULAR)
    day = last_completed_session(now)
    with svc.sf() as s:
        pf = svc.portfolio(s)
    rows: list[dict[str, Any]] = []
    live_at: list[datetime] = []
    for h in pf.holdings:
        q = svc._fresh_quote(h.ticker)
        t = toss.get(h.ticker) if h.source == "toss" else None
        row: dict[str, Any] = {"ticker": h.ticker, "quantity": h.quantity, "avg_price": h.cost_basis, "source": h.source}
        if t is not None:
            qty, last = float(t["quantity"]), _f(t.get("last_price"))
            live = q is not None and last is not None and taken is not None and q.timestamp > taken
            px = q.price if live else last
            d = qty * (px - last) if px is not None and last is not None else 0.0
            daily = _f(t.get("daily_pnl"))
            value, pnl = _f(t.get("market_value")), _f(t.get("pnl"))
            row |= {"price": px, "price_at": (q.timestamp if live else taken).isoformat() if (live or taken) else None, "live": live, "basis": "TOSS",
                    "purchase": _f(t.get("purchase_amount")), "value": value + d if value is not None else None, "pnl": pnl + d if pnl is not None else None,
                    "daily": (daily + d) if daily is not None and toss_fresh else None}
            if live:
                live_at.append(q.timestamp)
        else:
            cost = h.quantity * h.cost_basis
            if q is not None:
                prev = _prev_close(svc, h.ticker, q)
                px, daily, at = q.price, (h.quantity * (q.price - prev) if prev else None), q.timestamp
                live_at.append(q.timestamp)
            else:
                close, move, closed_at = _close_move(svc, h.ticker, day)
                # no live price: the last completed session's own move once it is over; during the pre-market and the
                # session today's change is unknown (never the last session's move shown as today's)
                px, daily, at = close, (h.quantity * move if move is not None and not in_session else None), closed_at
            row |= {"price": px, "price_at": at.isoformat() if at else None, "live": q is not None, "basis": "PRICE", "purchase": cost,
                    "value": h.quantity * px if px else None, "pnl": h.quantity * px - cost if px else None, "daily": daily}
        v, p, dl = row["value"], row["purchase"], row["daily"]
        row["pnl_rate"] = row["pnl"] / p if row["pnl"] is not None and p else None
        row["daily_rate"] = dl / (v - dl) if dl is not None and v and v - dl > 0 else None
        if fx:
            row |= {"value_krw": round(v * fx) if v is not None else None, "pnl_krw": round(row["pnl"] * fx) if row["pnl"] is not None else None,
                    "daily_krw": round(dl * fx) if dl is not None else None}
        rows.append(row)

    valued = [r for r in rows if r["value"] is not None]
    dailies = [r for r in rows if r["daily"] is not None]
    value = sum(r["value"] for r in valued)
    profits = [r for r in rows if r["pnl"] is not None and r["purchase"] is not None]
    purchase = sum(r["purchase"] for r in profits)
    pnl = sum(r["pnl"] for r in profits)
    daily = sum(r["daily"] for r in dailies)
    dbase = sum(r["value"] - r["daily"] for r in dailies if r["value"] is not None)
    totals = {"count": len(rows), "valued": len(valued), "daily_count": len(dailies), "value": round(value, 2) if valued else None,
              "pnl_count": len(profits), "purchase": round(purchase, 2) if profits else None, "pnl": round(pnl, 2) if profits else None,
              "pnl_rate": pnl / purchase if profits and purchase else None, "daily": round(daily, 2) if dailies else None,
              "daily_rate": daily / dbase if dailies and all(r["value"] is not None for r in dailies) and dbase > 0 else None}
    if fx:
        totals |= {"value_krw": round(value * fx) if valued else None, "pnl_krw": round(pnl * fx) if profits else None, "daily_krw": round(daily * fx) if dailies else None}
    notes = []
    if len(valued) < len(rows) or len(profits) < len(rows):
        notes.append(f"부분 합계: 평가금액 {len(valued)}/{len(rows)}종목, 손익 {len(profits)}/{len(rows)}종목 포함")
    if snap and not toss_fresh:
        notes.append("토스증권 동기화가 오래되어 오늘 손익은 빼고, 평가·손익만 최신 가격으로 계산했습니다")
    if len(dailies) < len(rows):
        notes.append(f"{len(rows) - len(dailies)}종목은 지금 가격이 없어 오늘 손익에서 뺐습니다")
    return {"at": now.isoformat(), "live": bool(live_at), "live_at": max(live_at).isoformat() if live_at else None,
            "synced_at": snap["taken_at"] if snap else None, "fx": {"rate": fx, "source": "토스증권 매매기준율", "at": fxd.get("at")} if fx else None,
            "rows": rows, "totals": totals, "notes": notes}
