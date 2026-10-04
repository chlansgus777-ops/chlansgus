"""내 매매 진단 / 내 매매 규칙 API (owner 2026-10-04). Reads the executions BrokerSync keeps (토스증권, read-only); the
only writes are the owner's own rules, notes and stops in this app's database, and two read-only Toss refreshes.
``sample=1`` answers from the built-in VIRTUAL sample instead (labelled is_sample) — never mixed with the account."""

from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from marketlens.application import habits as H
from marketlens.domain.habit_diagnosis import HoldingState, TradeRules, holding_plan
from marketlens.domain.habits import KST, Rules, StopPlan
from marketlens.domain.market_calendar import to_ny
from marketlens.infrastructure.db import repository as repo

router = APIRouter()


def _svc(req: Request) -> Any:
    from marketlens.api.routes import svc

    return svc(req)


def _get(s: Any, key: str, default: Any) -> Any:
    with s.sf() as ss:
        return H.load_json(lambda k: repo.get_setting(ss, k), key, default)


def _put(s: Any, key: str, value: Any) -> None:
    import json

    with s.sf() as ss:
        repo.set_setting(ss, key, json.dumps(value, ensure_ascii=False))
        ss.commit()


def _rules(s: Any) -> tuple[Rules, TradeRules, list[dict[str, Any]]]:
    d = _get(s, H.RULES_KEY, {})
    try:
        return Rules.from_dict(d.get("pattern")), TradeRules.from_dict(d.get("trade")), _get(s, H.RULES_HISTORY_KEY, [])
    except ValueError:
        return Rules(), TradeRules(), []


def _bars_fn(s: Any) -> H.BarsFn:
    from marketlens.api.routes import _view_bars

    def bars(sym: str, start: date, end: date) -> list[Any] | None:
        if sym[:1].isdigit():
            return None  # a Korean 6-character code (it starts with a digit): the app has no Korean daily bars
        got, _pending = _view_bars(s, [sym], start, end, wait=2.0)
        return got.get(sym) or None

    return bars


def _splits_fn(s: Any) -> H.SplitsFn:
    def splits(sym: str) -> list[date]:
        return [e.execution_date for e in (s.data.splits(sym) or [])]

    return splits


def _app_stop_fn(s: Any) -> H.AppStopFn:
    def app_stop(sym: str, before: datetime) -> StopPlan | None:
        with s.sf() as ss:
            rows = s.company_recommendations(ss, sym, before=before, limit=1)
            if not rows:
                return None
            r = rows[0]
            entry = (r.result or {}).get("entry") or {}
            if not entry.get("stop"):
                return None
            return StopPlan(float(entry["stop"]), float(entry["target1"]) if entry.get("target1") else None, r.as_of, "app")

    return app_stop


def _app_view(s: Any, sym: str) -> dict[str, Any]:
    with s.sf() as ss:
        rows = s.company_recommendations(ss, sym, limit=1)
        if not rows:
            return {}
        r = rows[0]
        res = r.result or {}
        lv = s.levels_now(r)
        return {"action": r.final_action, "stop": lv.get("stop"), "target": lv.get("target1"), "thesis_broken": bool(res.get("thesis_invalidated"))}


def _inputs(s: Any, sample: bool) -> dict[str, Any]:
    now = s.now()
    if sample:
        fills, holdings, bars = H.sample_inputs(now)
        return {"fills": fills, "holdings": holdings, "complete": True, "raw": {f.order_id: {"order_id": f.order_id, "symbol": f.symbol, "side": f.side,
                "quantity": str(f.quantity), "avg_price": str(f.price), "fees": str(f.fees), "filled_at": f.done_at.isoformat(), "source": "가상 샘플"} for f in fills},
                "bars_for": lambda sym, a, z: [b for b in bars.get(sym, []) if a <= b.day <= z] or None, "splits_for": lambda sym: [],
                "app_stop": None, "meta": {"label": "가상 샘플 — 실제 거래가 아닙니다"}, "source": "sample"}
    b = s.broker
    if b is None or not b.active():
        return {"fills": [], "holdings": None, "complete": False, "raw": {}, "bars_for": _bars_fn(s), "splits_for": _splits_fn(s), "app_stop": None,
                "meta": {"connected": False}, "source": "toss"}
    items = (b._fills or {}).get("items", [])
    fills, skipped = H.fills_from_toss(items)
    snap = b.snapshot() or {}
    st = b.status().get("fills") or {}
    return {"fills": fills, "holdings": snap.get("holdings"), "complete": bool(st.get("complete")), "raw": {f["order_id"]: f for f in items},
            "bars_for": _bars_fn(s), "splits_for": _splits_fn(s), "app_stop": _app_stop_fn(s), "source": "toss",
            "meta": {"connected": True, "fills": st, "snapshot_at": snap.get("taken_at"), "skipped": skipped,
                     "extended": (b._fills or {}).get("extended") or {}}}


def _app_holdings(s: Any) -> dict[str, Any]:
    """Without a connected broker: the US holdings entered in this app (a manual line or the trade records) with the
    trade records as executions, so the rules still say what to do with them. Used for the plans only — the
    diagnosis needs real execution times, which the records (a day, no time) do not have."""
    from decimal import Decimal

    from marketlens.domain.habits import Fill
    from marketlens.domain.market_calendar import NY

    with s.sf() as ss:
        pf = s.portfolio(ss)
        ledgers = s.ledger(ss)
    holdings = [{"symbol": h.ticker, "name": h.ticker, "market": "US", "currency": "USD", "quantity": h.quantity, "avg_price": h.cost_basis}
                for h in pf.holdings if h.quantity > 0 and h.cost_basis > 0]
    fills = []
    for g in ledgers:
        ts = g.get("trades") or []
        if not g.get("ticker") or g.get("splits") or any(t.kind == "SPLIT" for t in ts):
            continue  # a split changes the share basis (or the ticker moved on): plan from the holding alone
        for t in ts:
            if t.kind in ("BUY", "SELL") and t.quantity > 0:
                at = datetime.combine(t.day, datetime.min.time().replace(hour=16), tzinfo=NY)  # a day only: its close
                fills.append(Fill(f"L{t.id}", str(g["ticker"]), t.kind, Decimal(str(t.quantity)), Decimal(str(t.price)), Decimal(str(t.fees)),
                                  "USD", at, at, account="ledger", source="ledger"))
    return {"fills": fills, "holdings": holdings, "complete": True, "raw": {}, "bars_for": _bars_fn(s), "splits_for": _splits_fn(s),
            "app_stop": None, "source": "app", "meta": {"connected": False}}


def _plan_inputs(s: Any) -> dict[str, Any]:
    """The holdings the rules apply to: the broker account when connected, else what was entered in this app."""
    inp = _inputs(s, False)
    return inp if inp["meta"].get("connected") else _app_holdings(s)


def holding_states(s: Any, inp: dict[str, Any]) -> list[HoldingState]:
    """Each holding with what its plan needs and its price now (live quote → Toss price → latest close)."""
    holdings = inp["holdings"] or []
    if not holdings:
        return []
    prices: dict[str, tuple[float | None, str | None, str | None]] = {}
    highs: dict[str, float | None] = {}
    app: dict[str, dict[str, Any]] = {}
    states_pre = H.position_states(inp["fills"], holdings, {}, {}, {})
    for h, st in zip(holdings, states_pre):
        sym = h["symbol"]
        px, at, src = None, None, None
        if inp["source"] in ("toss", "app"):
            q = s._fresh_quote(sym) if h.get("market") == "US" else None
            if q is not None:
                px, at, src = q.price, q.timestamp.isoformat(), "실시간"
            elif h.get("last_price"):
                px, at, src = float(h["last_price"]), (s.broker.snapshot() or {}).get("taken_at"), "토스 동기화 시점 가격"
            try:
                app[sym] = _app_view(s, sym) if h.get("market") == "US" else {}
            except Exception:  # noqa: BLE001 - no analysis is "앱 분석 없음", never a failure
                app[sym] = {}
        opened = st.opened
        start = datetime.fromisoformat(opened).astimezone(KST).date() if opened else None
        bars = inp["bars_for"](sym, start, s.now().astimezone(KST).date()) if start else None
        if bars:
            highs[sym] = max(b.high for b in bars if b.day >= to_ny(datetime.fromisoformat(opened)).date()) if any(b.day >= to_ny(datetime.fromisoformat(opened)).date() for b in bars) else None
            if px is None:
                px, at, src = bars[-1].close, bars[-1].day.isoformat(), "최근 종가"
        if px is None and h.get("market") == "US":  # no live price and no history since opening: the latest close
            today = s.now().astimezone(KST).date()
            recent = inp["bars_for"](sym, today - timedelta(days=10), today)
            if recent:
                px, at, src = recent[-1].close, recent[-1].day.isoformat(), "최근 종가"
        if px is not None and highs.get(sym) is not None:
            highs[sym] = max(highs[sym] or px, px)
        prices[sym] = (px, at, src)
    return H.position_states(inp["fills"], holdings, prices, highs, app)


def plans(s: Any, inp: dict[str, Any], tr: TradeRules) -> list[dict[str, Any]]:
    """The mechanical plan of each holding (portfolio / stock page / home)."""
    holdings = inp["holdings"] or []
    out = []
    for st in holding_states(s, inp):
        p = holding_plan(st, tr)
        p["name"] = next((h.get("name") for h in holdings if h["symbol"] == st.symbol), st.symbol)
        p["market"] = next((h.get("market") for h in holdings if h["symbol"] == st.symbol), None)
        out.append(p)
    order = {"STOP": 0, "TRAIL": 1, "TAKE1": 2, "ADD": 3, "NO_PRICE": 4, "HOLD": 5}
    return sorted(out, key=lambda p: (order.get(p["action"], 9), p["symbol"]))


def rule_states(s: Any) -> tuple[list[HoldingState], TradeRules]:
    """The account's holdings and the saved trade rules, for the live rule alerts (US names: the ones with live prices)."""
    inp = _plan_inputs(s)
    _r, tr, _h = _rules(s)
    us = {h["symbol"] for h in inp["holdings"] or [] if h.get("market") == "US"}
    return [st for st in holding_states(s, inp) if st.symbol in us], tr


def wire_rule_watch(s: Any) -> None:
    """Give the service's rule watch its loader (the account lives behind the API layer's inputs)."""
    rw = getattr(s, "rule_watch", None)
    if rw is not None:
        rw.loader = lambda: rule_states(s)


def _report(s: Any, sample: bool) -> tuple[dict[str, Any], dict[str, Any]]:
    inp = _inputs(s, sample)
    rules, tr, hist = _rules(s)
    pl = plans(s, inp, tr) if (inp["holdings"] or []) else []
    rep = H.build_report(inp["fills"], inp["holdings"], inp["complete"], inp["bars_for"], inp["splits_for"], rules, tr, now=s.now(),
                         notes=_get(s, H.NOTES_KEY, {}) if not sample else {}, user_stops=_get(s, H.STOPS_KEY, {}) if not sample else {},
                         rules_history=hist if not sample else [], app_stop=inp["app_stop"], plans=pl, source=inp["source"], meta=inp["meta"])
    rep["plans"] = pl
    rep["rules_saved"] = tr.saved_at is not None
    rep["rule_conflicts"] = tr.conflicts()
    return rep, inp


@router.get("/habits")
def habits(req: Request, sample: bool = False) -> dict[str, Any]:
    rep, _ = _report(_svc(req), sample)
    return rep


@router.get("/habits/plans")
def habit_plans(req: Request, ticker: str | None = None) -> dict[str, Any]:
    """보유 종목의 '지금 할 일' (the saved rules on the account's holdings) — portfolio, stock page, home."""
    s = _svc(req)
    inp = _plan_inputs(s)
    _r, tr, _h = _rules(s)
    pl = plans(s, inp, tr)
    if ticker:
        pl = [p for p in pl if p["symbol"] == ticker.upper()]
    return {"plans": pl, "rules": tr.__dict__, "rules_saved": tr.saved_at is not None, "rule_conflicts": tr.conflicts(), "connected": inp["meta"].get("connected", False),
            "source": inp["source"], "at": s.now().isoformat()}


@router.get("/habits/trades/{trade_id}")
def habit_trade(req: Request, trade_id: str, sample: bool = False) -> dict[str, Any]:
    s = _svc(req)
    rep, inp = _report(s, sample)
    d = H.trade_detail(rep, trade_id, inp["fills"], inp["raw"], inp["bars_for"], _get(s, H.NOTES_KEY, {}) if not sample else {},
                       _get(s, H.STOPS_KEY, {}) if not sample else {})
    if d is None:
        raise HTTPException(404, "거래를 찾을 수 없습니다")
    return d


@router.get("/habits/export.csv")
def habit_csv(req: Request, sample: bool = False) -> PlainTextResponse:
    rep, _ = _report(_svc(req), sample)
    name = "marketlens_sample_trades.csv" if sample else "marketlens_my_trades.csv"
    return PlainTextResponse("﻿" + H.to_csv(rep), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="{name}"'})


class RulesIn(BaseModel):
    pattern: dict[str, Any] | None = None
    trade: dict[str, Any] | None = None


def _rules_changed(s: Any) -> None:
    rw = getattr(s, "rule_watch", None)
    if rw is not None and rw.loader is not None:
        threading.Thread(target=rw.load, daemon=True, name="rule-watch-load").start()


@router.put("/habits/rules")
def save_rules(req: Request, body: RulesIn) -> dict[str, Any]:
    """Saved rules: the analysis is recomputed with them at once; the trade rules apply to every holding from now on,
    and their stop counts as decided BEFORE every purchase made after this moment."""
    s = _svc(req)
    cur = _get(s, H.RULES_KEY, {})
    try:
        pat = Rules.from_dict(body.pattern if body.pattern is not None else cur.get("pattern"))
        merged = {**(body.trade if body.trade is not None else cur.get("trade") or {}), "saved_at": None}
        tr = TradeRules.checked(merged) if body.trade is not None else TradeRules.from_dict(merged)
    except (ValueError, TypeError) as e:
        raise HTTPException(422, str(e)) from None
    now = s.now().isoformat()
    trade = {k: v for k, v in tr.__dict__.items() if k != "saved_at"} | {"saved_at": now if body.trade is not None else (cur.get("trade") or {}).get("saved_at")}
    _put(s, H.RULES_KEY, {"pattern": pat.__dict__, "trade": trade})
    if body.trade is not None:
        hist = _get(s, H.RULES_HISTORY_KEY, [])
        hist.append({"saved_at": now, "stop_pct": tr.stop_pct, "take1_pct": tr.take1_pct})
        _put(s, H.RULES_HISTORY_KEY, hist[-200:])
    _rules_changed(s)
    return {"pattern": pat.__dict__, "trade": trade}


@router.delete("/habits/rules")
def reset_rules(req: Request) -> dict[str, Any]:
    s = _svc(req)
    _put(s, H.RULES_KEY, {})
    _rules_changed(s)
    return {"pattern": Rules().__dict__, "trade": TradeRules().__dict__}


class NoteIn(BaseModel):
    buy_reason: str = Field(default="", max_length=2000)
    during: str = Field(default="", max_length=2000)
    sell_reason: str = Field(default="", max_length=2000)


@router.put("/habits/notes/{trade_id}")
def save_note(req: Request, trade_id: str, body: NoteIn) -> dict[str, Any]:
    s = _svc(req)
    if len(trade_id) > 200:
        raise HTTPException(422, "거래 ID가 너무 깁니다")
    notes = _get(s, H.NOTES_KEY, {})
    notes[trade_id] = body.model_dump() | {"updated_at": s.now().isoformat()}
    _put(s, H.NOTES_KEY, notes)
    return notes[trade_id]


class StopIn(BaseModel):
    stop: float | None = Field(default=None, gt=0)
    target: float | None = Field(default=None, gt=0)


@router.put("/habits/stops/{buy_order_id}")
def save_stop(req: Request, buy_order_id: str, body: StopIn) -> dict[str, Any]:
    """A stop the owner enters for one purchase — dated now, so for a past purchase it is shown as 사후 입력."""
    s = _svc(req)
    stops = _get(s, H.STOPS_KEY, {})
    if body.stop is None:
        stops.pop(buy_order_id, None)
    else:
        stops[buy_order_id] = {"stop": body.stop, "target": body.target, "recorded_at": s.now().isoformat()}
    _put(s, H.STOPS_KEY, stops)
    return stops.get(buy_order_id) or {}


@router.post("/habits/refresh")
def refresh(req: Request) -> dict[str, Any]:
    """새 체결 조회: one read-only Toss sync now."""
    from marketlens.api.routes import _toss_call

    s = _svc(req)
    if s.broker is None or not s.broker.enabled:
        raise HTTPException(400, "토스증권 연동이 꺼져 있습니다")
    _toss_call(s.broker_sync_now)
    return {"ok": True, "fills": s.broker.status().get("fills")}


class ExtendIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=12)


@router.post("/habits/extend")
def extend(req: Request, body: ExtendIn) -> dict[str, Any]:
    """기간 확대 조회: every closed order of one name Toss still keeps (read-only), for a sale without its purchase."""
    from marketlens.api.routes import _toss_call

    s = _svc(req)
    if s.broker is None:
        raise HTTPException(400, "토스증권 연동이 꺼져 있습니다")
    return _toss_call(lambda: s.broker.extend_fills(body.symbol))

