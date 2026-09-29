"""오늘의 브리핑 (owner 2026-09-29: the morning briefing, "계속 고정되어 있으면 도움이 안 돼 — 오늘의 브리핑으로 바꾸고
실시간으로"; in the PC app only).

What a person checks through the day, live:
1. the market now (S&P 500 / Nasdaq-100 via SPY / QQQ: the live price against the previous close),
2. my account today (USD and %, the biggest movers — live prices where the feed has them),
3. holdings at or near their stop or first target (the live plans, the newest price),
4. what the live judge announced in the last 24 hours,
5. earnings and major events today and tomorrow for my names, and the best candidates of the list.
A figure without a live price uses the stored closes and says so; a part without data says so instead of disappearing.
Pure reads — it never starts an analysis or a provider request.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from marketlens.domain.enums import ACTION_KO, BULLISH_ACTIONS, Action
from marketlens.domain.market_calendar import last_completed_session, next_trading_day, to_ny
from marketlens.infrastructure.db import repository as repo

log = logging.getLogger("marketlens.briefing")
KST = ZoneInfo("Asia/Seoul")
NEAR = 0.03  # within 3 % of a stop or a target


def kst_day(now: datetime) -> date:
    return now.astimezone(KST).date()


def _closes(svc: Any, ticker: str, upto: date) -> list[tuple[date, float]]:
    # the stored closes only (LIVE): a briefing never waits on a provider — a slow network made it take 8 s; without a
    # store (MOCK) the data layer answers from its fixtures
    start = upto - timedelta(days=14)
    bars = svc.store.bars(ticker, start, upto) if getattr(svc, "store", None) is not None else (svc.data.bars(ticker, start, upto).value or [])
    return [(b.day, b.close) for b in bars if b.day <= upto and b.close]


def _move(svc: Any, ticker: str, day: date) -> tuple[float | None, float | None, float | None]:
    """(close on ``day``, the close before it, the change) — None when ``day`` has no stored close."""
    cl = _closes(svc, ticker, day)
    if len(cl) < 2 or cl[-1][0] != day:
        return None, None, None
    (_d0, c0), (_d1, c1) = cl[-2], cl[-1]
    return c1, c0, c1 / c0 - 1


def _live_move(svc: Any, ticker: str, day: date) -> tuple[float | None, float | None, float | None, datetime | None]:
    """(price, previous close, change, live time): the live price against the last close before its session when the
    feed has a fresh one (Toss 1 s / the stream), else the stored closes of ``day`` (live time None)."""
    fresh = getattr(svc, "_fresh_quote", None)
    q = fresh(ticker) if fresh is not None else None
    if q is not None and q.price:
        qday = to_ny(q.timestamp).date()
        before = qday if q.session.value in ("PREMARKET", "REGULAR") else qday + timedelta(days=1)  # after the close: vs today's close
        cl = [c for c in _closes(svc, ticker, qday) if c[0] < before]
        if cl:
            prev = cl[-1][1]
            return q.price, prev, q.price / prev - 1, q.timestamp
    c1, c0, ch = _move(svc, ticker, day)
    return c1, c0, ch, None


def build(svc: Any, now: datetime) -> dict[str, Any]:
    want = last_completed_session(now)
    out: dict[str, Any] = {"date_kst": kst_day(now).isoformat(), "ready": True, "built_at": now.isoformat(), "notes": []}
    # the session summed up: the newest one whose closes are stored (the daily bars of the night may come in later)
    try:
        spy = _closes(svc, "SPY", want)
    except Exception as e:  # noqa: BLE001 - reported below as "no closes"
        log.info("briefing SPY: %s", type(e).__name__)
        spy = []
    d = spy[-1][0] if spy else want
    out["session"] = d.isoformat()
    out["session_expected"] = want.isoformat()
    if spy and d < want:
        out["notes"].append(f"{want.month}/{want.day} 종가가 아직 저장되지 않아 {d.month}/{d.day} 종가 기준입니다")
    elif not spy:
        out["notes"].append("저장된 종가가 없어 시장·계좌 등락을 계산하지 못했습니다")

    # 1. market
    market = []
    live_at: list[datetime] = []
    for t, name in (("SPY", "S&P 500"), ("QQQ", "나스닥 100")):
        try:
            c1, _c0, ch, lt = _live_move(svc, t, d)
        except Exception as e:  # noqa: BLE001 - one missing index never hides the rest
            c1 = ch = lt = None
            log.info("briefing %s: %s", t, type(e).__name__)
        if lt is not None:
            live_at.append(lt)
        market.append({"name": name, "ticker": t, "close": c1, "change": ch, "live": lt is not None})
    out["market"] = market

    # 2. my account over the session
    with svc.sf() as s:
        pf = svc.portfolio(s)
        watched = [w.ticker for w in repo.watchlist(s)]
        scan = svc.shown_scan(s)
        recs = [r for r in repo.recommendations_for_scan(s, scan.id) if r.rank is not None] if scan is not None else []
        bull = {a.value for a in BULLISH_ACTIONS}
        bullish = [r for r in sorted(recs, key=lambda r: r.rank) if r.final_action in bull][:3]
        cands = []
        for r in bullish:
            try:
                mb = svc.levels_now(r).get("max_buy")  # today's share basis, as every other screen shows it
            except Exception as e:  # noqa: BLE001 - the candidate is still listed, without its limit
                log.info("briefing levels %s: %s", r.ticker, type(e).__name__)
                mb = None
            cands.append({"ticker": r.ticker, "action": r.final_action, "action_ko": ACTION_KO.get(Action(r.final_action), r.final_action), "score": r.score,
                          "max_buy": mb, "as_of": r.as_of.isoformat()})
    rows, pnl, base = [], 0.0, 0.0
    for h in pf.holdings:
        try:
            c1, c0, ch, lt = _live_move(svc, h.ticker, d)
        except Exception:  # noqa: BLE001
            c1 = c0 = ch = lt = None
        if lt is not None:
            live_at.append(lt)
        if c1 and c0:
            pnl += (c1 - c0) * h.quantity
            base += c0 * h.quantity
        rows.append({"ticker": h.ticker, "change": ch, "pnl": (c1 - c0) * h.quantity if c1 and c0 else None, "close": c1})
    movers = sorted((r for r in rows if r["change"] is not None), key=lambda r: -abs(r["change"]))[:3]
    out["account"] = {"holdings": len(pf.holdings), "priced": sum(1 for r in rows if r["change"] is not None), "pnl": round(pnl, 2) if base else None,
                      "change": pnl / base if base else None, "movers": movers}

    # 3. stops and targets (the live plans, at the newest price the app has)
    watch: list[dict[str, Any]] = []
    for h in pf.holdings:
        plan = svc.judge.plan(h.ticker)
        if plan is None or plan.stop is None:
            continue
        tr = svc.quotes.latest(h.ticker)
        px = tr.price if tr is not None else next((r["close"] for r in rows if r["ticker"] == h.ticker), None)
        if not px:
            continue
        to_stop = px / plan.stop - 1
        to_target = (px / plan.target1 - 1) if plan.target1 else None
        if to_stop <= NEAR:
            watch.append({"ticker": h.ticker, "kind": "STOP", "price": px, "level": plan.stop, "distance": to_stop,
                          "text": f"{h.ticker} 손절 기준 {'아래' if to_stop < 0 else '근처'} (현재 ${px:,.2f} · 손절 ${plan.stop:,.2f})"})
        elif to_target is not None and to_target >= -NEAR:
            watch.append({"ticker": h.ticker, "kind": "TARGET", "price": px, "level": plan.target1, "distance": to_target,
                          "text": f"{h.ticker} 1차 목표 {'도달' if to_target >= 0 else '근처'} (현재 ${px:,.2f} · 목표 ${plan.target1:,.2f})"})
    out["live"] = bool(live_at)
    out["live_at"] = max(live_at).isoformat() if live_at else None
    out["watch"] = sorted(watch, key=lambda w: (w["kind"] != "STOP", w["distance"]))

    # 4. overnight alerts (the last 24 hours)
    since = now - timedelta(hours=24)
    out["alerts"] = [a for a in svc.judge.alerts(limit=200) if a.get("kind") != "BRIEFING" and datetime.fromisoformat(a["at"]) >= since][-6:]

    # 5. today and tomorrow: my names' events and market-wide major events; the buy candidates of the last scan
    mine = {h.ticker for h in pf.holdings} | set(watched)
    ny_today = to_ny(now).date()
    horizon = next_trading_day(next_trading_day(ny_today - timedelta(days=1)))
    events = []
    cal = svc.calendar_view(wait=0.0)
    if cal.value is None:
        out["notes"].append("일정 데이터를 아직 받지 못함")
    for e in cal.value or []:
        if ny_today <= e.event_date <= horizon and ((set(e.affected) & mine) or (not e.affected and e.importance >= 0.7)):
            events.append({"date": e.event_date.isoformat(), "title": e.title, "tickers": sorted(set(e.affected) & mine), "type": str(getattr(e.event_type, "value", e.event_type))})
    out["events"] = sorted(events, key=lambda e: e["date"])[:8]
    out["candidates"] = cands
    out["scan_as_of"] = scan.as_of.isoformat() if scan is not None else None

    # one line for the alert and the card head
    parts = []
    spx = market[0]["change"]
    if spx is not None:
        parts.append(f"S&P 500 {spx * 100:+.1f}%")
    if out["account"]["pnl"] is not None:
        parts.append(f"내 계좌 {'+' if pnl >= 0 else '−'}${abs(pnl):,.0f} ({out['account']['change'] * 100:+.1f}%)")
    stops = sum(1 for w in out["watch"] if w["kind"] == "STOP")
    if stops:
        parts.append(f"손절 근처 {stops}종목")
    if out["events"]:
        parts.append(f"오늘·내일 일정 {len(out['events'])}건")
    out["headline"] = " · ".join(parts) if parts else "밤사이 확인할 변화가 없습니다"
    return out
