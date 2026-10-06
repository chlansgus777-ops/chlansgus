"""이번 달 할 일 (owner 2026-10-06: "제대로 된 수익루틴으로 쉽고 직관적이게") — the momentum book turned into a short
checklist against the owner's own account: what to sell, what to buy with how many shares for about how much, what to
leave alone, and when the next change is. The app never orders; the owner does it in the broker's app.

Pure: the caller supplies the book (application/momentum_book.py), the holdings, the cash and a price lookup.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Callable, Mapping, Sequence

SLOT = 0.05  # each holding of the book: 5 % of the account (20 slots)


def plan(book: Mapping[str, Any] | None, holdings: Sequence[tuple[str, float]], cash: float, cash_known: bool,
         price: Callable[[str], float | None], today: date) -> dict[str, Any]:
    if not book:
        return {"state": "COMPUTING", "headline": "이번 달 목록을 계산하는 중입니다"}
    nxt = date.fromisoformat(book["next_rebalance"])
    base = {"strategy": book.get("name"), "version": book.get("version"), "next_rebalance": book["next_rebalance"],
            "next_execute": book.get("next_execute"), "days_to_next": (nxt - today).days,
            "risk": "고위험 · 백테스트 기준 미달(최대 낙폭 −61%) · 앱은 주문하지 않습니다"}
    if book.get("state") != "READY":
        return base | {"state": "PREPARING", "headline": "이번 달 목록을 준비하는 중입니다", "reasons": list(book.get("reasons") or [])}

    held = {t: q for t, q in holdings if q > 0}
    targets = [h["ticker"] for h in book.get("holdings", [])]
    rank = {h["ticker"]: h.get("rank") for h in book.get("holdings", [])}
    dropped = set(book.get("sold") or [])
    sell = sorted(t for t in held if t in dropped)
    buy = [t for t in targets if t not in held]
    keep = [t for t in targets if t in held]
    outside = sorted(t for t in held if t not in targets and t not in dropped)

    unpriced: list[str] = []
    value = 0.0
    for t, q in held.items():
        p = price(t)
        if p is None:
            unpriced.append(t)
        else:
            value += q * p
    total = cash + value
    slot = total * SLOT
    buys = []
    for t in buy:
        p = price(t)
        shares = math.floor(slot / p) if p and p > 0 else None
        buys.append({"ticker": t, "rank": rank.get(t), "price": p, "shares": shares,
                     "amount": shares * p if shares is not None and p else None, "target_amount": slot})
    sells = [{"ticker": t, "shares": held[t], "price": price(t)} for t in sell]
    execute = date.fromisoformat(book["execute_day"])
    late = today > execute
    if buys or sells:
        parts = ([f"{len(sells)}종목 팔기"] if sells else []) + ([f"{len(buys)}종목 사기"] if buys else [])
        headline = "이번 달 할 일: " + ", ".join(parts)
        when = (f"규칙상 실행일은 {execute.isoformat()} 시가였습니다 — 지금 반영하면 그 사이 가격 변동만큼 차이가 납니다"
                if late else f"{execute.isoformat()} 미국장 시가에 맞춰 하세요")
        state = "ACT"
    else:
        headline = "이번 달 할 일 없음"
        when = f"다음 교체: {nxt.isoformat()} 종가 순위 → {book.get('next_execute')} 시가"
        state = "DONE"
    return base | {
        "state": state, "headline": headline, "when": when, "rebalance_day": book.get("rebalance_day"), "execute_day": book["execute_day"],
        "sell": sells, "buy": buys, "keep": keep, "outside": outside,
        "account": {"total": total, "cash": cash, "known": cash_known, "slot": slot, "unpriced": unpriced},
        "note": ("계좌 금액을 몰라 10만 달러 기준 예시입니다 — 설정에서 토스증권을 연결하거나 현금을 입력하면 내 계좌 기준으로 바뀝니다."
                 if not cash_known else "종목당 계좌의 5% 기준입니다. 주문은 증권사 앱에서 직접 하세요."),
    }
