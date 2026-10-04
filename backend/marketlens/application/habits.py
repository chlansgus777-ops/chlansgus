"""내 매매 진단 / 내 매매 규칙 — the service side: the executed orders (토스증권, read-only, already synced by
BrokerSync), the prices of the holding periods, and the owner's saved rules, notes and stops (app_settings).

``build_report`` turns them into what the 성과 › 내 매매 진단 tab shows; ``holding_plans`` into the "지금 할 일" of
the portfolio, the stock page and the home screen. A built-in VIRTUAL sample (``sample_inputs``) exists only to show
how the screens read — it is labelled 가상 샘플 everywhere and never mixed with the account's records.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Sequence

from marketlens.domain.habit_diagnosis import AddEvent, HoldingState, OrderResult, TradeRules, diagnose
from marketlens.domain.habits import (
    KST, EarlyExit, Fill, Match, Rules, StopPlan, TradeEval, dedupe, early_exit, evaluate, match_fifo, stop_review,
)
from marketlens.domain.market import Bar

RULES_KEY, RULES_HISTORY_KEY, NOTES_KEY, STOPS_KEY = "habits.rules", "habits.rules_history", "habits.notes", "habits.stops"
BarsFn = Callable[[str, date, date], list[Bar] | None]
SplitsFn = Callable[[str], list[date]]
AppStopFn = Callable[[str, datetime], StopPlan | None]


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _dt(v: Any) -> datetime | None:
    if not v:
        return None
    d = datetime.fromisoformat(str(v))
    return d if d.tzinfo else d.replace(tzinfo=KST)  # Toss times are KST


def fills_from_toss(items: Sequence[dict[str, Any]]) -> tuple[list[Fill], list[str]]:
    """Stored Toss executions → fills. An order without an average fill price is reported, never priced."""
    out: list[Fill] = []
    skipped: list[str] = []
    for it in items:
        q, p = _dec(it.get("quantity")), _dec(it.get("avg_price"))
        if q is None or q <= 0:
            continue
        if p is None or p <= 0:
            skipped.append(f"{it.get('symbol')} {it.get('side')} 주문(체결 수량 {q}): 평균 체결가가 없어 분석에서 제외")
            continue
        com, tax = _dec(it.get("commission")), _dec(it.get("tax"))
        out.append(Fill(str(it.get("order_id") or ""), str(it["symbol"]), str(it["side"]), q, p, (com or Decimal(0)) + (tax or Decimal(0)),
                        str(it.get("currency") or "OTHER"), _dt(it.get("filled_at")), _dt(it.get("ordered_at")) or datetime.now(timezone.utc),
                        source="toss", commission=com, tax=tax, status=str(it.get("status") or "")))
    return out, skipped


def opening_inventory(fills: Sequence[Fill], holdings: Sequence[dict[str, Any]] | None, complete: bool) -> tuple[dict[tuple[str, str, str], Decimal], list[str]]:
    """Shares held before the first fetched record: current holding − fetched buys + fetched sells (per symbol)."""
    if holdings is None:
        return {}, ["토스 보유 스냅샷이 없어 조회 기간 이전 보유분을 확인할 수 없습니다 — 매수 기록이 없는 매도는 '기초 매수 기록 필요'로 표시"]
    net: dict[tuple[str, str], Decimal] = {}
    for f in dedupe(fills):
        k = (f.symbol, f.currency)
        net[k] = net.get(k, Decimal(0)) + (f.quantity if f.side == "BUY" else -f.quantity)
    held = {(h["symbol"], h["currency"]): _dec(h.get("quantity")) or Decimal(0) for h in holdings}
    out: dict[tuple[str, str, str], Decimal] = {}
    warn: list[str] = []
    for k in set(net) | set(held):
        before = held.get(k, Decimal(0)) - net.get(k, Decimal(0))
        if before > Decimal("1e-9"):
            out[("toss", k[0], k[1])] = before
        elif before < Decimal("-1e-9"):
            warn.append(f"{k[0]}: 체결 기록과 현재 보유 수량이 맞지 않음({before}주) — 입고·이체·분할 또는 조회 범위 밖 체결 가능")
    if not complete:
        warn.append("체결 조회가 끝까지 완료되지 않아 조회 이전 보유분 계산이 정확하지 않을 수 있습니다")
    return out, warn


# ------------------------------------------------------------------ the report
def _f(d: Decimal | float | None, nd: int = 6) -> float | None:
    return None if d is None else round(float(d), nd)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _bars_window(bars_for: BarsFn, symbol: str, start: date, end: date) -> list[Bar] | None:
    try:
        return bars_for(symbol, start, end)
    except Exception:  # noqa: BLE001 - a price source failure is "판정 불가", never a crash
        return None


def stop_plan_for(m: Match, user_stops: dict[str, Any], history: Sequence[dict[str, Any]], app_stop: AppStopFn | None) -> StopPlan | None:
    """The stop that applied to a lot: one the owner entered for that purchase (dated when entered), else the saved
    rule in force BEFORE the purchase (its % below the fill price), else the app's analysis stop recorded before it."""
    b = m.buy
    if b is None:
        return None
    u = user_stops.get(b.order_id)
    if u and u.get("stop"):
        return StopPlan(float(u["stop"]), float(u["target"]) if u.get("target") else None, datetime.fromisoformat(u["recorded_at"]), "user")
    before = [h for h in history if datetime.fromisoformat(h["saved_at"]) <= b.done_at]
    if before:
        h = before[-1]
        return StopPlan(float(b.price) * (1 + float(h["stop_pct"]) / 100), float(b.price) * (1 + float(h["take1_pct"]) / 100),
                        datetime.fromisoformat(h["saved_at"]), "rule")
    if app_stop is not None:
        try:
            return app_stop(b.symbol, b.done_at)
        except Exception:  # noqa: BLE001
            return None
    return None


def build_report(fills: Sequence[Fill], holdings: Sequence[dict[str, Any]] | None, complete: bool, bars_for: BarsFn, splits_for: SplitsFn,
                 rules: Rules, trade_rules: TradeRules, *, now: datetime, notes: dict[str, Any] | None = None, user_stops: dict[str, Any] | None = None,
                 rules_history: Sequence[dict[str, Any]] = (), app_stop: AppStopFn | None = None, plans: Sequence[dict[str, Any]] = (),
                 source: str = "toss", meta: dict[str, Any] | None = None) -> dict[str, Any]:
    opening, warnings = opening_inventory(fills, holdings, complete)
    matches, open_lots, w2 = match_fifo(fills, opening)
    warnings += w2
    today = now.astimezone(KST).date()
    evals: list[TradeEval] = []
    early: dict[str, EarlyExit] = {}
    stops: dict[str, Any] = {}
    bars_cache: dict[str, list[Bar] | None] = {}
    splits_cache: dict[str, list[date]] = {}
    span: dict[str, tuple[date, date]] = {}
    for m in matches:  # one price request per symbol: the whole span of its trades and the observation days after
        a = (m.buy.done_at if m.buy else m.sell.ordered_at).astimezone(KST).date() - timedelta(days=7)
        z = min(today, m.sell.done_at.astimezone(KST).date() + timedelta(days=21))
        lo, hi = span.get(m.symbol, (a, z))
        span[m.symbol] = (min(lo, a), max(hi, z))
    for sym, (a, z) in span.items():
        bars_cache[sym] = _bars_window(bars_for, sym, a, z)
        try:
            splits_cache[sym] = list(splits_for(sym))
        except Exception:  # noqa: BLE001
            splits_cache[sym] = []
    for m in matches:
        te = evaluate(m, rules, bars_cache.get(m.symbol), splits_cache.get(m.symbol, []))
        evals.append(te)
        sid = m.sell.order_id or m.id
        if sid not in early:
            early[sid] = early_exit(m.sell.market, m.sell, bars_cache.get(m.symbol), rules, splits_cache.get(m.symbol, []))
        stops[m.id] = stop_review(te, stop_plan_for(m, user_stops or {}, rules_history, app_stop))

    # sale orders: one per order however many lots / fills
    orders: dict[str, list[TradeEval]] = {}
    for te in evals:
        orders.setdefault(te.match.sell.order_id or te.match.id, []).append(te)
    order_rows: list[dict[str, Any]] = []
    results: list[OrderResult] = []
    avg_cost_view = _average_cost_view(fills, opening)
    for oid, tes in orders.items():
        s = tes[0].match.sell
        known = [t for t in tes if t.match.buy is not None]
        need = [t for t in tes if t.match.buy is None]
        pats = [t.pattern for t in known]
        q_known = sum((t.match.quantity for t in known), Decimal(0))
        q_cand = sum((t.match.quantity for t in known if t.pattern == "CANDIDATE"), Decimal(0))
        if need:
            status = "NEED_BASIS"
        elif "CANDIDATE" in pats and len(set(pats)) == 1:
            status = "CANDIDATE"
        elif "CANDIDATE" in pats:
            status = "PARTIAL"
        elif "INSUFFICIENT" in pats:
            status = "INSUFFICIENT"
        elif "UNCONFIRMED" in pats:
            status = "UNCONFIRMED"
        else:
            status = "NOT"
        net = sum(t.net_pnl or 0.0 for t in known)
        gross = sum(t.gross_pnl or 0.0 for t in known)
        cost = sum((t.entry or 0.0) * float(t.match.quantity) + float(t.match.buy_fees) for t in known)
        fees = sum(float(t.match.buy_fees + t.match.sell_fees) for t in known)
        maes = [t.mae for t in known if t.mae is not None]
        mfes = [t.mfe for t in known if t.mfe is not None]
        stop_worst = _worst_stop([stops[t.match.id].status for t in tes])
        ee = early[oid]
        row = {"order_id": oid, "symbol": s.symbol, "currency": s.currency, "sold_at": _iso(s.done_at), "ordered_at": _iso(s.ordered_at),
               "quantity": _f(s.quantity), "price": _f(s.price), "rows": len(tes), "known_quantity": _f(q_known),
               "need_basis_quantity": _f(sum((t.match.quantity for t in need), Decimal(0))), "pattern": status,
               "candidate_quantity": _f(q_cand), "net_pnl": round(net, 4) if known else None, "gross_pnl": round(gross, 4) if known else None,
               "net_ret": round(net / cost * 100, 4) if known and cost > 0 else None, "fees": round(fees, 4),
               "mae": min(maes) if maes else None, "mfe": max(mfes) if mfes else None, "early": asdict(ee), "stop": stop_worst,
               "avg_cost_view": avg_cost_view.get(oid), "lots": [t.match.id for t in tes]}
        order_rows.append(row)
        if known and not need and cost > 0:
            hd = [t.holding_days or 0.0 for t in known]
            results.append(OrderResult(oid, s.symbol, s.currency, net, net / cost * 100, max(hd), min(maes) if maes else None,
                                       max(mfes) if mfes else None, status, ee.status, stop_worst, fees, gross))
    order_rows.sort(key=lambda r: r["sold_at"] or "", reverse=True)

    # pattern counts per sale order; the denominator: orders whose price data allowed a verdict
    evaluable = [r for r in order_rows if r["pattern"] in ("CANDIDATE", "PARTIAL", "NOT", "UNCONFIRMED")]
    cands = [r for r in order_rows if r["pattern"] == "CANDIDATE"]
    by_sym: dict[str, dict[str, Any]] = {}
    for r in order_rows:
        b = by_sym.setdefault(r["symbol"], {"symbol": r["symbol"], "currency": r["currency"], "sell_orders": 0, "evaluable": 0, "candidates": 0,
                                            "partial": 0, "insufficient": 0, "early": 0, "stop_review": 0, "net_pnl": 0.0})
        b["sell_orders"] += 1
        b["evaluable"] += r in evaluable
        b["candidates"] += r["pattern"] == "CANDIDATE"
        b["partial"] += r["pattern"] == "PARTIAL"
        b["insufficient"] += r["pattern"] in ("INSUFFICIENT", "NEED_BASIS")
        b["early"] += r["early"]["status"] == "CANDIDATE"
        b["stop_review"] += r["stop"] in ("TOUCHED_HELD", "EXIT_BELOW")
        b["net_pnl"] += r["net_pnl"] or 0.0
    for b in by_sym.values():
        b["ratio"] = b["candidates"] / b["evaluable"] if b["evaluable"] else None
        b["repeated"] = b["candidates"] >= rules.repeat_min
        b["net_pnl"] = round(b["net_pnl"], 4)

    adds = _add_events(fills, opening, order_rows)
    open_losers = [p for p in plans if p.get("action") == "STOP" and p.get("pnl_pct") is not None]
    diag = diagnose(results, adds, open_losers, trade_rules)
    realized: dict[str, dict[str, float]] = {}
    for r in order_rows:
        if r["net_pnl"] is None:
            continue
        c = realized.setdefault(r["currency"], {"net": 0.0, "gross": 0.0, "fees": 0.0})
        c["net"] += r["net_pnl"]
        c["gross"] += r["gross_pnl"] or 0.0
        c["fees"] += r["fees"]
    known_orders = [r for r in order_rows if r["net_pnl"] is not None and r["pattern"] != "NEED_BASIS"]
    times = [f.done_at for f in fills]
    summary = {
        "sell_orders": len(order_rows), "matched_rows": len(evals),
        "realized": {k: {kk: round(vv, 4) for kk, vv in v.items()} for k, v in realized.items()},
        "profit_orders": sum(1 for r in known_orders if r["net_pnl"] > 0), "known_orders": len(known_orders),
        "profit_ratio": (sum(1 for r in known_orders if r["net_pnl"] > 0) / len(known_orders)) if known_orders else None,
        "breakeven": {"candidates": len(cands), "partial": sum(1 for r in order_rows if r["pattern"] == "PARTIAL"),
                      "unconfirmed": sum(1 for r in order_rows if r["pattern"] == "UNCONFIRMED"), "evaluable": len(evaluable),
                      "ratio": len(cands) / len(evaluable) if evaluable else None,
                      "not_evaluable": sum(1 for r in order_rows if r["pattern"] in ("INSUFFICIENT", "NEED_BASIS")),
                      "repeated": len(cands) >= rules.repeat_min},
        "early_exit": {"candidates": sum(1 for r in order_rows if r["early"]["status"] == "CANDIDATE"),
                       "evaluable": sum(1 for r in order_rows if r["early"]["status"] in ("CANDIDATE", "NOT"))},
        "stop_review": {"candidates": sum(1 for r in order_rows if r["stop"] in ("TOUCHED_HELD", "EXIT_BELOW")),
                        "no_stop": sum(1 for r in order_rows if r["stop"] == "NO_STOP")},
        "data_short": sum(1 for r in order_rows if r["pattern"] in ("INSUFFICIENT", "NEED_BASIS")),
        "count_unit": "매도 주문 1건 = 1회 (여러 매수 lot·부분 체결로 나뉘어도 한 번)",
    }
    trades = [_trade_row(te, stops[te.match.id], early.get(te.match.sell.order_id or te.match.id), notes or {}) for te in evals]
    trades.sort(key=lambda t: t["sold_at"] or "", reverse=True)
    return {
        "source": source, "is_sample": source == "sample", "generated_at": now.isoformat(),
        "period": {"first": _iso(min(times)) if times else None, "last": _iso(max(times)) if times else None},
        "meta": meta or {}, "rules": asdict(rules), "trade_rules": asdict(trade_rules), "summary": summary, "diagnosis": diag,
        "by_symbol": sorted(by_sym.values(), key=lambda b: (-b["candidates"], b["symbol"])), "orders": order_rows, "trades": trades,
        "open_lots": [{"symbol": o.symbol, "currency": o.currency, "quantity": _f(o.quantity), "buy_order_id": o.buy.order_id if o.buy else None,
                       "entry": _f(o.buy.price) if o.buy else None, "bought_at": _iso(o.buy.done_at) if o.buy else None,
                       "basis": "체결 기록" if o.buy else "조회 이전 보유분(매수 기록 필요)"} for o in open_lots],
        "warnings": warnings,
    }


def _worst_stop(sts: Sequence[str]) -> str:
    for s in ("EXIT_BELOW", "TOUCHED_HELD", "NOT_TOUCHED", "INSUFFICIENT", "NO_STOP"):
        if s in sts:
            return s
    return "NO_STOP"


def _trade_row(te: TradeEval, sr: Any, ee: EarlyExit | None, notes: dict[str, Any]) -> dict[str, Any]:
    m = te.match
    p = te.path
    return {
        "id": m.id, "symbol": m.symbol, "currency": m.currency, "sell_order_id": m.sell.order_id, "buy_order_id": m.buy.order_id if m.buy else None,
        "row_of_order": m.split_index, "bought_at": _iso(m.buy.done_at) if m.buy else None, "sold_at": _iso(m.sell.done_at),
        "sell_ordered_at": _iso(m.sell.ordered_at), "quantity": _f(m.quantity), "entry": te.entry, "exit": te.exit,
        "holding_days": round(te.holding_days, 3) if te.holding_days is not None else None,
        "gross_pnl": round(te.gross_pnl, 4) if te.gross_pnl is not None else None, "net_pnl": round(te.net_pnl, 4) if te.net_pnl is not None else None,
        "gross_ret": round(te.gross_ret, 4) if te.gross_ret is not None else None, "net_ret": round(te.net_ret, 4) if te.net_ret is not None else None,
        "buy_fees": _f(m.buy_fees), "sell_fees": _f(m.sell_fees), "mae": round(te.mae, 4) if te.mae is not None else None,
        "mfe": round(te.mfe, 4) if te.mfe is not None else None, "pattern": te.pattern, "pattern_reason": te.pattern_reason,
        "path": None if p is None else {"status": p.status, "reason": p.reason, "first_bar": _iso_d(p.first_bar), "last_bar": _iso_d(p.last_bar),
                                        "missing_days": [d.isoformat() for d in p.missing_days], "interval": "1일(일봉)", "notes": p.notes,
                                        "uncertain_low": p.uncertain_low[1] if p.uncertain_low else None,
                                        "uncertain_high": p.uncertain_high[1] if p.uncertain_high else None},
        "stop": asdict(sr), "early": asdict(ee) if ee else None, "has_note": bool(notes.get(m.id)), "source": m.sell.source,
    }


def _iso_d(d: date | None) -> str | None:
    return d.isoformat() if d else None


def _average_cost_view(fills: Sequence[Fill], opening: dict[tuple[str, str, str], Decimal]) -> dict[str, dict[str, Any]]:
    """For each sale: the whole position's average cost at that moment (the Toss app's way) next to the FIFO lots —
    with adds in between the two can differ. Unknown while shares of unknown price were still in the position."""
    out: dict[str, dict[str, Any]] = {}
    pos: dict[tuple[str, str], tuple[Decimal, Decimal, bool]] = {}  # qty, cost, cost known
    for (_a, sym, cur), q in opening.items():
        pos[(sym, cur)] = (q, Decimal(0), False)
    for f in dedupe(fills):
        k = (f.symbol, f.currency)
        q, c, ok = pos.get(k, (Decimal(0), Decimal(0), True))
        if f.side == "BUY":
            pos[k] = (q + f.quantity, c + f.quantity * f.price + f.fees, ok)
            continue
        if f.side != "SELL":
            continue
        avg = (c / q) if q > 0 and ok else None
        if avg is not None and avg > 0:
            net = (f.price * f.quantity - f.fees) - avg * f.quantity
            out[f.order_id or f.ordered_at.isoformat()] = {"avg_cost": _f(avg), "net_ret": round(float(net / (avg * f.quantity)) * 100, 4)}
        else:
            out[f.order_id or f.ordered_at.isoformat()] = {"avg_cost": None, "net_ret": None}
        if q > 0:
            take = min(f.quantity, q)
            pos[k] = (q - take, c - (c / q) * take, ok) if q - take > Decimal("1e-9") else (Decimal(0), Decimal(0), True)
    return out


def _add_events(fills: Sequence[Fill], opening: dict[tuple[str, str, str], Decimal], orders: Sequence[dict[str, Any]]) -> list[AddEvent]:
    """Purchases into a position already held, with how far below/above its average cost they were bought."""
    out: list[tuple[AddEvent, str, int]] = []
    pos: dict[tuple[str, str], tuple[Decimal, Decimal, bool, int]] = {}
    gen: dict[tuple[str, str], int] = {}
    for (_a, sym, cur), q in opening.items():
        pos[(sym, cur)] = (q, Decimal(0), False, 0)
    closed_net: dict[tuple[str, int], float] = {}
    for f in dedupe(fills):
        k = (f.symbol, f.currency)
        q, c, ok, g = pos.get(k, (Decimal(0), Decimal(0), True, gen.get(k, 0)))
        if f.side == "BUY":
            if q > 0 and ok and c > 0:
                avg = c / q
                out.append((AddEvent(f.symbol, float(f.price / avg - 1) * 100, None), f.symbol, g))
            pos[k] = (q + f.quantity, c + f.quantity * f.price + f.fees, ok, g)
        elif f.side == "SELL" and q > 0:
            take = min(f.quantity, q)
            if q - take <= Decimal("1e-9"):
                pos[k] = (Decimal(0), Decimal(0), True, g + 1)
                gen[k] = g + 1
                net = sum((o["net_pnl"] or 0.0) for o in orders if o["symbol"] == f.symbol)
                closed_net[(f.symbol, g)] = net
            else:
                pos[k] = (q - take, c - (c / q) * take, ok, g)
    return [AddEvent(e.symbol, e.below_avg_pct, closed_net.get((sym, g))) for e, sym, g in out]


def trade_detail(report: dict[str, Any], trade_id: str, fills: Sequence[Fill], raw: dict[str, dict[str, Any]], bars_for: BarsFn,
                 notes: dict[str, Any], user_stops: dict[str, Any]) -> dict[str, Any] | None:
    t = next((x for x in report["trades"] if x["id"] == trade_id), None)
    if t is None:
        return None
    order = next((o for o in report["orders"] if o["order_id"] == (t["sell_order_id"] or t["id"].split("~")[0])), None)
    start = datetime.fromisoformat(t["bought_at"] or t["sell_ordered_at"]).astimezone(KST).date() - timedelta(days=14)
    end = datetime.fromisoformat(t["sold_at"]).astimezone(KST).date() + timedelta(days=14)
    bars = _bars_window(bars_for, t["symbol"], start, end) or []
    calc = []
    if t["entry"] is not None:
        calc += [f"진입가 {t['entry']:g} = 매수 주문 {t['buy_order_id'] or '-'}의 평균 체결가",
                 f"청산가 {t['exit']:g} = 매도 주문 {t['sell_order_id'] or '-'}의 평균 체결가",
                 f"수량 {t['quantity']:g}주 (FIFO: 이 매도 주문의 {t['row_of_order'] + 1}번째 매칭 행)",
                 f"비용 반영 전 손익 = ({t['exit']:g} − {t['entry']:g}) × {t['quantity']:g} = {t['gross_pnl']:+,.4f}",
                 f"수수료·세금 배분: 매수 {t['buy_fees']:g} + 매도 {t['sell_fees']:g} (각 주문 전체 비용 × 매칭 수량 / 주문 체결 수량)",
                 f"순손익 = {t['net_pnl']:+,.4f} · 순수익률 = 순손익 / (진입가 × 수량 + 매수 비용) = {t['net_ret']:+.2f}%"]
        if t["mae"] is not None:
            calc.append(f"MAE {t['mae']:+.2f}% / MFE {t['mfe']:+.2f}% — 매수 후·매도 주문 전의 시점이 확실한 일봉 관측치 기준")
    else:
        calc.append("매수 기록이 없어 손익을 계산하지 않음(임의의 매수가를 쓰지 않음) — '기간 확대 조회'로 실제 매수를 찾을 수 있습니다")
    return {
        "trade": t, "order": order, "is_sample": report["is_sample"], "calc": calc,
        "bars": [{"day": b.day.isoformat(), "open": b.open, "high": b.high, "low": b.low, "close": b.close} for b in bars],
        "raw": {"buy": raw.get(t["buy_order_id"] or ""), "sell": raw.get(t["sell_order_id"] or "")},
        "note": notes.get(trade_id) or {}, "user_stop": user_stops.get(t["buy_order_id"] or "") or None,
        "basis_note": ("이 매도 주문은 여러 매수 lot에 FIFO로 나뉘었습니다. 토스 앱의 평균단가(전체 포지션) 기준 수익률은 "
                       "lot별 수익률과 다를 수 있습니다." if order and order["rows"] > 1 else None),
    }


CSV_FIELDS = ["symbol", "currency", "buy_order_id", "sell_order_id", "bought_at", "sold_at", "quantity", "entry", "exit", "holding_days",
              "gross_pnl", "net_pnl", "gross_ret", "net_ret", "buy_fees", "sell_fees", "mae", "mfe", "pattern", "pattern_reason"]


def to_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    buf.write(("# 가상 샘플 — 실제 거래 아님\n" if report["is_sample"] else "# 토스증권 체결 기록(읽기 전용) 분석\n"))
    w = csv.DictWriter(buf, fieldnames=CSV_FIELDS, extrasaction="ignore")
    w.writeheader()
    for t in report["trades"]:
        w.writerow(t)
    return buf.getvalue()


# ------------------------------------------------------------------ the owner's settings
def load_json(get: Callable[[str], str | None], key: str, default: Any) -> Any:
    raw = get(key)
    try:
        return json.loads(raw) if raw else default
    except (TypeError, ValueError):
        return default


def position_states(fills: Sequence[Fill], holdings: Sequence[dict[str, Any]], prices: dict[str, tuple[float | None, str | None, str | None]],
                    highs: dict[str, float | None], app: dict[str, dict[str, Any]]) -> list[HoldingState]:
    """Each current holding with what its plan needs: when the position opened, its first purchase, adds and partial
    sales since, and the high since then."""
    out = []
    by_sym: dict[str, list[Fill]] = {}
    for f in dedupe(fills):
        by_sym.setdefault(f.symbol, []).append(f)
    for h in holdings:
        sym, qty, avg = h["symbol"], float(h["quantity"]), float(h["avg_price"])
        fs = by_sym.get(sym, [])
        # walk back from now: the current position opened after the last time it was empty
        total = Decimal(str(h["quantity"]))
        opened_idx = None
        run = total
        for i in range(len(fs) - 1, -1, -1):
            f = fs[i]
            run = run - f.quantity if f.side == "BUY" else run + f.quantity
            if run <= Decimal("1e-9"):
                opened_idx = i
                break
        since = fs[opened_idx:] if opened_idx is not None else fs
        buys = [f for f in since if f.side == "BUY"]
        sells = [f for f in since if f.side == "SELL"]
        first = buys[0] if (opened_idx is not None and buys) else None
        px, at, src = prices.get(sym, (None, None, None))
        a = app.get(sym) or {}
        out.append(HoldingState(sym, h["currency"], qty, avg, px, at, src, float(first.quantity) if first else None,
                                _iso(first.done_at) if first else None, max(0, len(buys) - (1 if first else 0)), bool(sells),
                                highs.get(sym), a.get("action"), a.get("stop"), a.get("target"), bool(a.get("thesis_broken"))))
    return out


# ------------------------------------------------------------------ the VIRTUAL sample (never the account)
def sample_inputs(now: datetime) -> tuple[list[Fill], list[dict[str, Any]], dict[str, list[Bar]]]:
    """A made-up account (가상 샘플) that shows each pattern once or twice; its prices are made up too."""
    from marketlens.domain.market_calendar import NY, add_trading_days, last_completed_session

    end = last_completed_session(now)
    d0 = add_trading_days(end, -80)
    bars: dict[str, list[Bar]] = {}
    fills: list[Fill] = []

    def path(sym: str, closes: list[float]) -> None:
        out, d = [], d0
        for c in closes:
            out.append(Bar(d, c * 0.998, c * 1.006, c * 0.994, c, 1e6))
            d = add_trading_days(d, 1)
        bars[sym] = out

    def at(sym: str, i: int, hh: int = 11) -> datetime:
        return datetime.combine(bars[sym][i].day, datetime.min.time().replace(hour=hh), tzinfo=NY)

    def fill(oid: str, sym: str, side: str, q: str, p: float, i: int, fee: str = "0.5") -> None:
        t = at(sym, i)
        fills.append(Fill(oid, sym, side, Decimal(q), Decimal(str(p)), Decimal(fee), "USD", t, t - timedelta(minutes=1), source="sample"))

    # SAMP1: 100 → 95.5 → 101 (break-even exit), twice
    a = [100.0] * 3 + [98, 96.5, 95.5, 96, 97.5, 99, 100.4, 101] + [101.5] * 6 + [100, 97, 95.8, 96.5, 98, 99.5, 100.8] + [102, 104, 107, 109, 110, 110] + [110] * 47
    path("SAMP1", a[:81])
    fill("S1-B1", "SAMP1", "BUY", "10", 100.0, 1)
    fill("S1-S1", "SAMP1", "SELL", "10", 101.0, 10)
    fill("S1-B2", "SAMP1", "BUY", "10", 101.0, 16)
    fill("S1-S2", "SAMP1", "SELL", "10", 101.5, 23)
    # SAMP2: averaging down then a large loss
    b = [50.0] * 3 + [49, 47, 46, 45, 44, 43, 42, 41, 40, 39, 41, 40.5] + [40] * 66
    path("SAMP2", b[:81])
    fill("S2-B1", "SAMP2", "BUY", "20", 50.0, 1)
    fill("S2-B2", "SAMP2", "BUY", "20", 45.0, 6)
    fill("S2-S1", "SAMP2", "SELL", "40", 40.0, 13)
    # SAMP3: a clean winner sold early (then +8 % within 5 days)
    c = [30.0] * 3 + [30.5, 31, 31.6, 32, 32.6, 33, 34, 35, 35.6, 36] + [36] * 68
    path("SAMP3", c[:81])
    fill("S3-B1", "SAMP3", "BUY", "30", 30.0, 1)
    fill("S3-S1", "SAMP3", "SELL", "30", 32.6, 7)
    # SAMP4: still held, below its stop
    d = [80.0] * 30 + [79, 77, 75, 74, 73, 72] + [72] * 45
    path("SAMP4", d[:81])
    fill("S4-B1", "SAMP4", "BUY", "5", 80.0, 20)
    holdings = [{"symbol": "SAMP4", "currency": "USD", "quantity": "5", "avg_price": "80.1", "market": "US", "name": "가상 샘플 4"}]
    return fills, holdings, bars
