"""이번 달 할 일 (owner 2026-10-06: "제대로 된 수익루틴으로 쉽고 직관적이게") — the momentum book turned into a short
checklist against the owner's own account: what to sell, what to buy with how many shares for about how much, what to
leave alone, and when the next change is. The app never orders; the owner does it in the broker's app.

What the account can actually do (owner-supplied check 2026-10-06: a $5,000 cash account was told to buy $100,000):
- The money for buys is the cash plus what this month's sells bring in, after the 0.15 % cost on each side.
- Each buy aims at 5 % of the account (the strategy's slot) in whole shares, cost included, in the book's rank order.
  When the money runs out, a buy is cut to what is left or not made, and the card says how much is missing and why.
- A buy that would take its sector over the account's sector limit (portfolio limits, 30 %) is not made.
- Trades the account's limits change are listed as deviations: the strategy's own record (the forward paper book)
  keeps the rule as written; the card never pretends the account follows it exactly.

Where the strategy and the owner's own trade rules (보유 종목 계획: stop, trailing stop, first take, add) disagree on a
name, the card shows both and which one each acts on — it never merges them into one buy or sell.

Pure: the caller supplies the book (application/momentum_book.py), the holdings, the cash, a price lookup and, when it
has them, each holding's sector and the owner's rule action for it.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Callable, Mapping, Sequence

SLOT = 0.05  # each holding of the book: 5 % of the account (20 slots)
COST = 0.0015  # a side, as the backtest and the paper book
MAX_SECTOR = 0.30  # the account's sector limit (domain.portfolio.PortfolioLimits.max_sector)
RULE_SELLS = {"STOP": "손절", "TRAIL": "추적 손절", "TAKE1": "1차 익절"}


def _m(x: float) -> str:
    return f"${x:,.0f}"


def plan(book: Mapping[str, Any] | None, holdings: Sequence[tuple[str, float]], cash: float, cash_known: bool,
         price: Callable[[str], float | None], today: date, sectors: Mapping[str, str] | None = None,
         rule_actions: Mapping[str, Mapping[str, Any]] | None = None, max_sector: float = MAX_SECTOR) -> dict[str, Any]:
    if not book:
        return {"state": "COMPUTING", "headline": "이번 달 목록을 계산하는 중입니다"}
    nxt = date.fromisoformat(book["next_rebalance"])
    base = {"strategy": book.get("name"), "version": book.get("version"), "next_rebalance": book["next_rebalance"],
            "next_execute": book.get("next_execute"), "days_to_next": (nxt - today).days,
            "risk": "고위험 · 백테스트 기준 미달(최대 낙폭 −61%) · 앱은 주문하지 않습니다"}
    if book.get("state") != "READY":
        return base | {"state": "PREPARING", "headline": "이번 달 목록을 준비하는 중입니다", "reasons": list(book.get("reasons") or [])}

    held = {t: q for t, q in holdings if q > 0}
    rows = {h["ticker"]: h for h in book.get("holdings", [])}
    targets = list(rows)
    rank = {t: r.get("rank") for t, r in rows.items()}
    sector = {**(sectors or {}), **{t: r.get("sector") or "Unknown" for t, r in rows.items()}}
    dropped = set(book.get("sold") or [])
    sell = sorted(t for t in held if t in dropped)
    buy = [t for t in targets if t not in held]
    keep = [t for t in targets if t in held]
    outside = sorted(t for t in held if t not in targets and t not in dropped)

    px = {t: price(t) for t in set(held) | set(buy)}
    unpriced = sorted(t for t in held if px.get(t) is None)
    # a holding without a price leaves the account total unknown: no 5 % amount is computed from a partial total
    # (independent review 2 F04: an unpriced holding shrank the total and every amount with it)
    value = {t: q * px[t] for t, q in held.items() if px.get(t) is not None}
    total = cash + sum(value.values()) if not unpriced else None
    slot = total * SLOT if total is not None else None

    sells = [{"ticker": t, "shares": held[t], "price": px.get(t),
              "proceeds": held[t] * px[t] * (1 - COST) if px.get(t) is not None else None} for t in sell]
    proceeds = sum(s["proceeds"] or 0.0 for s in sells)
    available = cash + proceeds
    sector_value: dict[str, float] = {}
    for t, v in value.items():
        if t not in sell:
            sector_value[sector.get(t, "Unknown")] = sector_value.get(sector.get(t, "Unknown"), 0.0) + v

    buys: list[dict[str, Any]] = []
    deviations: list[dict[str, Any]] = []
    left = available
    needed_full = 0.0
    for t in buy:
        p = px.get(t)
        row = {"ticker": t, "rank": rank.get(t), "price": p, "sector": sector.get(t, "Unknown"), "target_amount": slot,
               "shares": None, "amount": None, "status": "OK", "reason": None}
        if slot is None or not p or p <= 0:
            row["status"] = "WAIT"
            row["reason"] = "가격 없음" if slot is not None else "계좌 총액 확인 대기"
            buys.append(row)
            continue
        full = math.floor(slot / (p * (1 + COST)))  # the strategy's 5 %, cost included, whole shares
        needed_full += full * p * (1 + COST)
        sec = sector.get(t, "Unknown")
        if full > 0 and total and (sector_value.get(sec, 0.0) + full * p) / total > max_sector + 1e-12:
            row |= {"status": "SECTOR", "shares": 0, "amount": 0.0,
                    "reason": f"{sec} 업종이 계좌의 {max_sector:.0%}를 넘게 됨(지금 {sector_value.get(sec, 0.0) / total:.0%})"}
            deviations.append({"ticker": t, "kind": "SECTOR", "detail": f"{t}: 업종 한도로 사지 않음 — " + row["reason"]})
            buys.append(row)
            continue
        fit = min(full, math.floor(left / (p * (1 + COST)))) if left > 0 else 0
        row["shares"] = fit
        row["amount"] = fit * p
        if full == 0:
            row |= {"status": "TOO_PRICEY", "reason": "1주 가격이 계좌의 5%보다 큼"}
        elif fit < full:
            short = (full - fit) * p * (1 + COST)
            row |= {"status": "CASH", "reason": f"현금 부족 — {full}주 중 {fit}주만 가능(약 {_m(short)} 모자람)" if fit else f"현금 부족 — 약 {_m(short)} 모자람"}
            deviations.append({"ticker": t, "kind": "CASH", "detail": f"{t}: " + row["reason"]})
        left -= fit * p * (1 + COST)
        sector_value[sec] = sector_value.get(sec, 0.0) + fit * p
        buys.append(row)
    planned = sum((b["amount"] or 0.0) * (1 + COST) for b in buys)
    shortfall = max(0.0, needed_full - available) if total is not None else None

    # the owner's own rules on the same names: shown side by side, never merged
    conflicts: list[dict[str, Any]] = []
    todo: list[dict[str, Any]] = []
    for t, a in (rule_actions or {}).items():
        act = a.get("action")
        if t in keep and act in RULE_SELLS:
            conflicts.append({"ticker": t, "strategy": f"유지 — 이번 달 순위 {rank.get(t) or '21~40'}위", "rule": f"{a.get('action_ko', RULE_SELLS[act])} — {a.get('detail', '')}",
                              "basis": "전략에는 손절·익절이 없습니다. 내 규칙을 따르면 이 종목은 전략 모의기록과 달라집니다 — 어느 쪽을 따를지 직접 정하세요."})
        elif t in sell and act == "ADD":
            conflicts.append({"ticker": t, "strategy": "팔기 — 이번 달 목록에서 빠짐", "rule": f"{a.get('action_ko', '추가 매수')} — {a.get('detail', '')}",
                              "basis": "전략은 팔라고 하고 내 규칙은 더 사라고 합니다 — 어느 쪽을 따를지 직접 정하세요."})
        elif act in RULE_SELLS and t not in sell:
            todo.append({"ticker": t, "kind": "RULE", "text": f"{t} {a.get('action_ko', RULE_SELLS[act])} — {a.get('detail', '')}", "source": "내 규칙"})
    for c in conflicts:
        todo.append({"ticker": c["ticker"], "kind": "CONFLICT", "text": f"{c['ticker']} 판단이 엇갈림 — 전략: {c['strategy']} / 내 규칙: {c['rule']}", "source": "전략·내 규칙"})
    for s in sells:
        todo.append({"ticker": s["ticker"], "kind": "SELL", "text": f"{s['ticker']} {s['shares']:g}주 팔기 — 이번 달 목록에서 빠짐", "source": "전략"})
    nbuy = sum(1 for b in buys if b["shares"])
    if nbuy:
        todo.append({"ticker": None, "kind": "BUY", "text": f"{nbuy}종목 사기 — 약 {_m(planned)}" + (f" (자금 부족으로 {len(deviations)}건 줄임)" if deviations else ""), "source": "전략"})
    order = {"RULE": 0, "CONFLICT": 1, "SELL": 2, "BUY": 3}
    todo.sort(key=lambda x: order[x["kind"]])

    execute = date.fromisoformat(book["execute_day"])
    late = today > execute
    if nbuy or sells:
        parts = ([f"{len(sells)}종목 팔기"] if sells else []) + ([f"{nbuy}종목 사기"] if nbuy else [])
        headline = "이번 달 할 일: " + ", ".join(parts)
        when = (f"규칙상 실행일은 {execute.isoformat()} 시가였습니다 — 지금 반영하면 그 사이 가격 변동만큼 차이가 납니다"
                if late else f"{execute.isoformat()} 미국장 시가에 맞춰 하세요")
        state = "ACT"
    elif buy and total is None:
        headline = f"이번 달 {len(buy)}종목을 사야 하지만 수량을 아직 계산할 수 없습니다"
        when = "보유 종목 가격을 받으면 다시 계산합니다"
        state = "WAIT"
    elif buy:
        headline = f"이번 달 {len(buy)}종목을 사야 하지만 지금 계좌로는 살 수 없습니다"
        when = "아래 이유(현금 부족·업종 한도)를 확인하세요 — 현금을 넣거나 무엇을 팔지는 직접 정하세요"
        state = "BLOCKED"
    else:
        headline = "이번 달 할 일 없음"
        when = f"다음 교체: {nxt.isoformat()} 종가 순위 → {book.get('next_execute')} 시가"
        state = "DONE"
    outside_value = sum(value.get(t, 0.0) for t in outside)
    return base | {
        "state": state, "headline": headline, "when": when, "rebalance_day": book.get("rebalance_day"), "execute_day": book["execute_day"],
        "sell": sells, "buy": buys, "keep": keep, "outside": outside, "conflicts": conflicts, "deviations": deviations, "today": todo[:5],
        "account": {"total": total, "cash": cash, "known": cash_known, "slot": slot, "unpriced": unpriced},
        "funding": {"cash": cash, "sell_proceeds": proceeds, "available": available, "planned": planned, "needed_full": needed_full if total is not None else None,
                    "shortfall": shortfall, "cost_rate": COST, "outside_value": outside_value,
                    "outside_share": outside_value / total if total else None},
        "warning": (f"보유 종목 {', '.join(unpriced)}의 가격을 받지 못해 계좌 총액을 계산할 수 없습니다 — 살 수량은 가격을 받은 뒤 표시합니다."
                    if unpriced else None),
        "note": ("계좌 금액을 몰라 10만 달러 기준 예시입니다 — 설정에서 토스증권을 연결하거나 현금을 입력하면 내 계좌 기준으로 바뀝니다."
                 if not cash_known else "종목당 계좌의 5%(비용 0.15% 포함) 기준, 쓸 수 있는 현금 안에서 계산했습니다. 주문은 증권사 앱에서 직접 하세요."),
    }
