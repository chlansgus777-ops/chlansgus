"""Won-based return of a US holding, split into what the stock did and what the dollar did (owner 2026-09-29:
"주가로 +8%, 환율로 +3%처럼 수익이 어디서 났는지").

For a holding of Q shares at average cost C (USD), valued at P (USD), bought at a share-weighted won rate F0 and valued
at the rate F now:
- cost in won      = Q·C·F0
- value in won     = Q·P·F
- stock effect     = Q·(P − C)·F0      (the dollar gain, at the rate it was bought with)
- currency effect  = Q·P·(F − F0)      (what the rate change did to today's dollar value)
The two effects add up exactly to value − cost. F0 is found by replaying the dated buys and sales with the average-cost
method (a sale keeps the average, as brokers and Korean tax practice do); the replay must reproduce the holding's own
dollar cost (±1.5 %, fees and rounding), otherwise the purchase rate is not known and nothing is guessed.

The rate of a purchase is the broker's own when it is known (토스증권: its buy rate at the moment of the execution),
else the day's reference rate (FRED, New York noon) — ``basis`` says which.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Callable, Mapping, Sequence

COST_TOLERANCE = 0.015
FX_LOOKBACK_DAYS = 7  # a purchase on a US holiday or a day FRED skipped takes the last rate before it


@dataclass(frozen=True, slots=True)
class Lot:
    day: date
    kind: str  # BUY / SELL
    quantity: float
    price: float  # USD per share
    fees: float = 0.0
    fx: float | None = None  # KRW per USD the broker quoted at this execution, when known


@dataclass(frozen=True, slots=True)
class FxAttribution:
    ticker: str
    known: bool
    reason: str | None = None  # why not known
    buy_fx: float | None = None
    cost_krw: float | None = None
    value_krw: float | None = None
    stock_krw: float | None = None
    fx_krw: float | None = None
    total_krw: float | None = None
    stock_pct: float | None = None  # P / C − 1
    fx_pct: float | None = None  # F / F0 − 1
    total_pct: float | None = None  # (P·F) / (C·F0) − 1
    basis: str | None = None  # BROKER (every purchase at the broker's rate) / REFERENCE (the day's rate) / MIXED


def rate_on(rates: Mapping[date, float], d: date) -> float | None:
    for k in range(FX_LOOKBACK_DAYS + 1):
        r = rates.get(d - timedelta(days=k))
        if r:
            return r
    return None


def purchase_rate(lots: Sequence[Lot], rates: Mapping[date, float]) -> tuple[float | None, float, float, str | None]:
    """(share-weighted purchase rate of what is still held, quantity held, dollar cost held, reason if unknown)."""
    f0, qty, usd, why, _basis = _replay(lots, rates)
    return f0, qty, usd, why


def _replay(lots: Sequence[Lot], rates: Mapping[date, float]) -> tuple[float | None, float, float, str | None, str | None]:
    qty = usd = krw = 0.0
    kinds: set[str] = set()
    for lot in sorted(lots, key=lambda x: (x.day, x.kind != "BUY")):
        if lot.kind == "BUY":
            r = lot.fx if lot.fx and lot.fx > 0 else rate_on(rates, lot.day)
            if r is None:
                return None, qty, usd, f"{lot.day.isoformat()} 환율 없음", None
            kinds.add("BROKER" if lot.fx and lot.fx > 0 else "REFERENCE")
            amount = lot.quantity * lot.price + lot.fees
            qty, usd, krw = qty + lot.quantity, usd + amount, krw + amount * r
        elif lot.kind == "SELL" and qty > 0:
            keep = max(0.0, 1 - min(lot.quantity, qty) / qty)
            qty, usd, krw = qty * keep, usd * keep, krw * keep
    if usd <= 0:
        return None, qty, usd, "남은 매수 기록 없음", None
    return krw / usd, qty, usd, None, (kinds.pop() if len(kinds) == 1 else "MIXED")


def attribute(ticker: str, quantity: float, avg_cost: float, price: float | None, fx_now: float | None,
              lots: Sequence[Lot] | None, rates: Mapping[date, float]) -> FxAttribution:
    if price is None or not price > 0:
        return FxAttribution(ticker, False, "평가 가격 없음")
    if fx_now is None or not fx_now > 0:
        return FxAttribution(ticker, False, "현재 환율 없음")
    if not lots:
        return FxAttribution(ticker, False, "매수일 기록이 없어 매수 당시 환율을 알 수 없음(직접 입력한 줄)")
    f0, _qty, usd, why, basis = _replay(lots, rates)
    if f0 is None:
        return FxAttribution(ticker, False, why)
    own = quantity * avg_cost
    if own <= 0 or abs(usd - own) > COST_TOLERANCE * own:
        return FxAttribution(ticker, False, "매수 기록의 금액이 지금 보유의 매입금액과 맞지 않음(오래된 매수 기록 누락 등)")
    cost, value = quantity * avg_cost * f0, quantity * price * fx_now
    stock, fx = quantity * (price - avg_cost) * f0, quantity * price * (fx_now - f0)
    # whole won, and the parts add up exactly on screen: total = stock + fx, value = cost + total
    c, st, fxk = round(cost), round(stock), round(fx)
    return FxAttribution(ticker, True, None, round(f0, 4), c, c + st + fxk, st, fxk, st + fxk,
                         round(price / avg_cost - 1, 6), round(fx_now / f0 - 1, 6), round(value / cost - 1, 6), basis)


def totals(rows: Sequence[FxAttribution]) -> dict[str, float | int | None]:
    k = [r for r in rows if r.known]
    if not k:
        return {"count": 0, "cost_krw": None, "stock_krw": None, "fx_krw": None, "total_krw": None, "stock_pct": None, "fx_pct": None, "total_pct": None}
    cost = sum(r.cost_krw or 0 for r in k)
    s, f = sum(r.stock_krw or 0 for r in k), sum(r.fx_krw or 0 for r in k)
    return {"count": len(k), "cost_krw": cost, "stock_krw": s, "fx_krw": f, "total_krw": s + f,
            "stock_pct": s / cost if cost else None, "fx_pct": f / cost if cost else None, "total_pct": (s + f) / cost if cost else None}


RateSource = Callable[[date, date], Mapping[date, float]]
