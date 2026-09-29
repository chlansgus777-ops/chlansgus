"""The broker account (토스증권) inside the portfolio — one rule, pure and deterministic.

When an account is connected, it is the truth for what it holds: the account's quantity and average price replace an
entered line or the trade records of the same stock (the records' realized result and dividends stay with it). A
holding the account does not have is kept — it may be held at another broker — but marked ``outside_broker`` so the
screen can ask whether it was sold. Only US stocks join the analysis (MarketLens analyses US stocks); domestic (KR)
holdings are shown on their own in won and never mixed into the dollar totals. Anything left out is said in a note.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Callable

from marketlens.domain.portfolio import Holding


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    ticker: str
    name: str
    market: str  # US / KR / OTHER
    currency: str  # USD / KRW / OTHER
    quantity: float
    avg_price: float


Profile = Callable[[str], tuple[str, tuple[str, ...], float]]  # ticker → (sector, themes, rate sensitivity)


def merge_broker(own: list[Holding], positions: list[BrokerPosition], profile: Profile, broker: str = "토스증권") -> tuple[list[Holding], list[str]]:
    """``own``: holdings from entered lines and trade records. Returns the holdings to analyse and the notes."""
    notes: list[str] = []
    us: dict[str, BrokerPosition] = {}
    for p in positions:
        if p.quantity <= 0:
            continue
        if p.market == "US" and p.currency == "USD":
            if p.ticker in us:  # the spec lists a stock once; two lines would be summed at their weighted average
                a = us[p.ticker]
                q = a.quantity + p.quantity
                us[p.ticker] = replace(a, quantity=q, avg_price=(a.quantity * a.avg_price + p.quantity * p.avg_price) / q)
            else:
                us[p.ticker] = p
        elif p.market not in ("US", "KR"):
            notes.append(f"{broker} 계좌의 {p.name}({p.ticker}): 미국·국내 주식이 아닌 항목이라 분석과 합계에서 뺐습니다")
    out: list[Holding] = []
    carried: dict[str, Holding] = {}
    for h in own:
        if h.ticker in us:
            carried[h.ticker] = h
            b = us[h.ticker]
            if abs(b.quantity - h.quantity) > 1e-6 or abs(b.avg_price - h.cost_basis) > max(0.005, 1e-6 * h.cost_basis):
                what = "거래 기록" if h.source == "ledger" else "직접 입력"
                notes.append(f"{h.ticker}: {broker} 계좌 기준 {b.quantity:g}주·평단 ${b.avg_price:,.2f} ({what} {h.quantity:g}주·${h.cost_basis:,.2f}는 계산에 쓰지 않음)")
            continue
        out.append(replace(h, outside_broker=True))
    for t, b in us.items():
        sector, themes, rates = profile(t)
        prev = carried.get(t)
        out.append(Holding(t, b.quantity, b.avg_price, sector, themes, rates, source="toss",
                           realized_pnl=prev.realized_pnl if prev else 0.0, dividends=prev.dividends if prev else 0.0))
    outside = [h.ticker for h in out if h.outside_broker]
    if outside:
        notes.append(f"{broker} 계좌에 없는 보유 {len(outside)}개({', '.join(outside)}): 직접 입력하거나 거래 기록으로 남긴 종목입니다. "
                     "다른 증권사에 있다면 그대로 두고, 이미 판 종목이면 보유 표에서 지우세요")
    out.sort(key=lambda h: (h.source != "toss", h.ticker))
    return out, notes
