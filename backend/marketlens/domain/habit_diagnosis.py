"""내 매매 진단 and 내 매매 규칙 — from the owner's own closed trades: why the account is not making money, what to
change, and for each holding the mechanical levels (stop, take-profit, adding) with what the rule says to do now
(owner 2026-10-04: "왜 지금 수익을 못내고 손해를 보고있는지 고치려면 어떻게 해야하는지 … 기계적으로 추매,매도,손절해야할때
도움을 주는 기능").

Everything is arithmetic on executed orders and observed prices; a finding cites its numbers and its sample size, and
says nothing when there are too few trades to say it. The rules are the owner's: suggested values come from the
owner's own trades (e.g. how deep the winners dipped), and nothing is applied until the owner saves them. MarketLens
never places an order — "지금 할 일" is what the saved rule says, for the owner to execute or not.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from statistics import median
from typing import Any, Sequence

MIN_TRADES = 5  # below this many closed sale orders a pattern is not called a habit


@dataclass(frozen=True)
class TradeRules:
    """The owner's mechanical rules for a position (defaults until the owner saves their own)."""

    stop_pct: float = -7.0  # sell everything at this % below the average cost
    use_app_stop: bool = False  # when the app's analysis has a stop ABOVE the % stop, use that one
    take1_pct: float = 10.0  # first take-profit at this % above the average cost …
    take1_fraction: float = 0.5  # … selling this fraction
    trail_pct: float = -8.0  # after the first take-profit (or once the price reached it): sell the rest this far below the high
    add_mode: str = "winners_only"  # winners_only (불타기만) / none / any (물타기 허용)
    add_trigger_pct: float = 5.0  # add only at least this % above the average cost (winners_only)
    add_fraction: float = 0.5  # of the first purchase's quantity
    max_adds: int = 1
    saved_at: str | None = None  # None: the defaults, never saved by the owner

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> "TradeRules":
        base = TradeRules()
        if not d:
            return base
        conv = {k: (type(getattr(base, k))(v) if getattr(base, k) is not None else v) for k, v in d.items() if hasattr(base, k) and v is not None}
        r = replace(base, **conv)
        if not (-50 < r.stop_pct < 0) or not (0 < r.take1_pct < 500) or not (0 < r.take1_fraction <= 1) or not (-50 < r.trail_pct < 0):
            raise ValueError("손절은 0보다 작게(−50% 초과), 1차 익절은 0보다 크게, 매도 비율은 0~1, 추적 손절은 0보다 작게 입력하세요")
        if r.add_mode not in ("winners_only", "none", "any") or not (0 < r.add_fraction <= 2) or not (0 <= r.max_adds <= 5) or r.add_trigger_pct < 0:
            raise ValueError("추가매수 방식·비율·횟수를 확인하세요")
        return r


# ------------------------------------------------------------------ diagnosis
@dataclass
class OrderResult:
    """One closed sale order (its whole matched quantity with a known purchase)."""

    order_id: str
    symbol: str
    currency: str
    net_pnl: float
    net_ret: float  # %
    holding_days: float
    mae: float | None  # % (worst lot)
    mfe: float | None
    pattern: str  # CANDIDATE / PARTIAL / NOT / UNCONFIRMED / INSUFFICIENT
    early: str  # CANDIDATE / NOT / INSUFFICIENT
    stop: str  # NO_STOP / TOUCHED_HELD / EXIT_BELOW / NOT_TOUCHED / INSUFFICIENT
    fees: float
    gross_pnl: float


@dataclass
class AddEvent:
    """A purchase into a position already held."""

    symbol: str
    below_avg_pct: float  # % vs the running average cost (negative: averaging down)
    position_net: float | None  # the net result of that position once closed (None while open)


def _pct(xs: Sequence[float], q: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, max(0, int(round(q * (len(s) - 1)))))]


def diagnose(orders: Sequence[OrderResult], adds: Sequence[AddEvent], open_losers: Sequence[dict[str, Any]], rules: TradeRules) -> dict[str, Any]:
    closed = [o for o in orders]
    n = len(closed)
    out: dict[str, Any] = {"closed": n, "findings": [], "suggested": None, "headline": None, "stats": None}
    if n == 0:
        out["headline"] = "매수 기록이 있는 매도 체결이 아직 없어 진단할 거래가 없습니다."
        return out
    wins = [o for o in closed if o.net_pnl > 0]
    losses = [o for o in closed if o.net_pnl < 0]
    avg_win = sum(o.net_ret for o in wins) / len(wins) if wins else None
    avg_loss = sum(o.net_ret for o in losses) / len(losses) if losses else None
    win_rate = len(wins) / n
    expectancy = sum(o.net_ret for o in closed) / n
    by_cur: dict[str, dict[str, float]] = {}
    for o in closed:
        c = by_cur.setdefault(o.currency, {"net": 0.0, "gross": 0.0, "fees": 0.0, "loss": 0.0, "profit": 0.0})
        c["net"] += o.net_pnl
        c["gross"] += o.gross_pnl
        c["fees"] += o.fees
        c["loss" if o.net_pnl < 0 else "profit"] += o.net_pnl
    hold_w = median([o.holding_days for o in wins]) if wins else None
    hold_l = median([o.holding_days for o in losses]) if losses else None
    out["stats"] = {"win_rate": win_rate, "avg_win_pct": avg_win, "avg_loss_pct": avg_loss, "expectancy_pct": expectancy,
                    "payoff": (avg_win / -avg_loss) if avg_win and avg_loss else None, "by_currency": by_cur,
                    "median_hold_days_win": hold_w, "median_hold_days_loss": hold_l, "wins": len(wins), "losses": len(losses)}
    few = n < MIN_TRADES
    F: list[dict[str, Any]] = []

    def add(fid: str, title: str, weight: float, evidence: str, fix: str) -> None:
        F.append({"id": fid, "title": title, "weight": round(weight, 4), "evidence": evidence, "fix": fix})

    # 1. losses larger than gains
    if avg_win is not None and avg_loss is not None and -avg_loss > avg_win:
        add("payoff", "이익은 작게, 손실은 크게", (-avg_loss - avg_win) * len(losses) / n,
            f"이긴 매도 {len(wins)}건 평균 {avg_win:+.1f}%, 진 매도 {len(losses)}건 평균 {avg_loss:+.1f}% — 승률 {win_rate:.0%}로는 거래당 {expectancy:+.2f}%",
            f"손실을 이익보다 작게 끊어야 합니다: 손절 {rules.stop_pct:g}%를 기계적으로 지키고, 이익은 1차 익절({rules.take1_pct:+g}%) 뒤 추적 손절로 더 끌고 가세요.")
    elif losses and not wins:
        add("payoff", "모든 매도가 손실", 1.0, f"매도 {n}건 모두 손실, 평균 {avg_loss:+.1f}%", "진입 기준부터 다시 보세요: 앱 판정이 매수(BUY/BUY SMALL)일 때만, 손절가를 정한 뒤에 사세요.")
    # 2. a few large losses
    big = [o for o in losses if o.net_ret <= rules.stop_pct]
    for cur, c in by_cur.items():
        lost = -c["loss"]
        big_c = [o for o in big if o.currency == cur]
        if lost > 0 and big_c:
            share = -sum(o.net_pnl for o in big_c) / lost
            if share >= 0.4:
                add(f"big_losses_{cur}", "큰 손실 몇 건이 손실 대부분", share,
                    f"{rules.stop_pct:g}%보다 크게 잃은 매도 {len(big_c)}건({', '.join(sorted({o.symbol for o in big_c}))})이 {cur} 실현 손실의 {share:.0%}",
                    f"손절가({rules.stop_pct:g}%)에 닿으면 기다리지 말고 정리하는 규칙 하나로 이 손실 대부분을 줄일 수 있었습니다(아래 '내 매매 규칙').")
    # 3. holding losers longer
    if hold_w is not None and hold_l is not None and hold_l > hold_w * 1.5 and hold_l - hold_w >= 2:
        add("hold_losers", "손실 종목을 더 오래 들고 있음", min(1.0, (hold_l - hold_w) / max(hold_l, 1)),
            f"보유 기간 중앙값: 이긴 매도 {hold_w:.1f}일, 진 매도 {hold_l:.1f}일",
            "빠지는 종목을 '회복할 때까지' 기다리는 대신 손절가에서 정리하고, 오르는 종목은 1차 익절 뒤 나머지를 추적 손절로 들고 가세요.")
    # 4. break-even exits
    be = [o for o in closed if o.pattern == "CANDIDATE"]
    be_eval = [o for o in closed if o.pattern in ("CANDIDATE", "NOT", "PARTIAL", "UNCONFIRMED")]
    if be:
        syms: dict[str, int] = {}
        for o in be:
            syms[o.symbol] = syms.get(o.symbol, 0) + 1
        rep = [f"{k} {v}회" for k, v in sorted(syms.items(), key=lambda x: -x[1])]
        add("breakeven", "물렸다가 본전 근처에서 탈출", len(be) / max(1, len(be_eval)),
            f"평가 가능한 매도 {len(be_eval)}건 중 {len(be)}건이 매수 후 {''}하락을 거쳐 본전 근처(순수익률 −0.5~+3%)에서 매도 — {', '.join(rep)}",
            "하락을 버틴 뒤 본전에 파는 매매는 손실 위험만 지고 이익은 거의 없습니다. 매수할 때 손절가와 1차 익절가를 먼저 정하고, 그 가격에서만 파세요.")
    # 5. averaging down
    downs = [a for a in adds if a.below_avg_pct <= -3.0]
    if downs:
        closed_downs = [a for a in downs if a.position_net is not None]
        bad = [a for a in closed_downs if a.position_net is not None and a.position_net < 0]
        add("average_down", "물타기(평단보다 낮게 추가매수)", len(downs) / max(1, len(adds)),
            f"추가매수 {len(adds)}번 중 {len(downs)}번이 평단보다 3% 이상 낮은 가격" + (f" — 정리된 {len(closed_downs)}건 중 {len(bad)}건이 손실로 끝남" if closed_downs else ""),
            "추가매수는 '수익이 난 종목에만(불타기)' 규칙으로 바꾸세요. 손실 중인 종목에 돈을 더 넣으면 손절이 더 어려워집니다.")
    # 6. early exits
    ee = [o for o in wins if o.early == "CANDIDATE"]
    ee_eval = [o for o in wins if o.early in ("CANDIDATE", "NOT")]
    if ee and len(ee) / max(1, len(ee_eval)) >= 0.3:
        add("early_exit", "이익을 일찍 끊음(사후 비교)", len(ee) / max(1, len(ee_eval)),
            f"이긴 매도 중 관찰 가능한 {len(ee_eval)}건 중 {len(ee)}건은 매도 후 5거래일 안에 매도가보다 5% 이상 더 오름(사후 가격 비교 — 당시 판단이 틀렸다는 뜻은 아님)",
            f"전량 매도 대신 1차 익절({rules.take1_fraction:.0%})만 하고 나머지는 고점 대비 {rules.trail_pct:g}% 추적 손절로 들고 가세요.")
    # 7. stop discipline
    no_stop = [o for o in closed if o.stop == "NO_STOP"]
    touched = [o for o in closed if o.stop in ("TOUCHED_HELD", "EXIT_BELOW")]
    if touched:
        add("stop_kept", "정한 손절가를 지나서도 보유", len(touched) / n,
            f"손절가가 있었던 거래 중 {len(touched)}건은 관측 가격이 손절가 아래로 내려간 뒤에도 보유하거나 손절가 아래에서 매도",
            "손절가에 닿으면 그날 정리하는 것을 규칙으로 하세요. 앱이 '지금 할 일: 손절'로 알려줍니다.")
    if len(no_stop) == n:
        add("no_stop", "사전 손절가 없이 매수", 0.3,
            f"매도 {n}건 모두 매수 전에 기록된 손절가가 없음",
            "아래 '내 매매 규칙'을 저장하면 보유 종목마다 손절가가 자동으로 정해지고, 다음 매매부터 기록됩니다.")
    # 8. fees
    for cur, c in by_cur.items():
        if c["fees"] > 0 and c["gross"] > 0 and c["fees"] / c["gross"] >= 0.3:
            add(f"fees_{cur}", "수수료·세금이 이익을 갉아먹음", c["fees"] / c["gross"],
                f"{cur} 매매 차익 {c['gross']:,.2f} 중 비용 {c['fees']:,.2f} ({c['fees'] / c['gross']:.0%})", "짧게 자주 사고파는 횟수를 줄이세요.")
    # open losers beyond the stop
    if open_losers:
        add("open_losers", "지금 손절가 아래에 있는 보유 종목", 2.0,
            ", ".join(f"{x['symbol']} {x['pnl_pct']:+.1f}%" for x in open_losers),
            "규칙대로라면 정리할 종목입니다 — 포트폴리오의 '지금 할 일'을 확인하세요.")
    F.sort(key=lambda f: -f["weight"])
    for f in F:
        # a holding below its stop is a fact about today, not an estimate from past sales
        f["confidence"] = "현재 상태" if f["id"] == "open_losers" else "표본 적음" if few else "충분"
    out["findings"] = F
    top = F[0]["title"] if F else None
    out["headline"] = (f"매도 {n}건 · 승률 {win_rate:.0%} · 평균 이익 {avg_win:+.1f}% / 평균 손실 {avg_loss:+.1f}% · 거래당 {expectancy:+.2f}%"
                       if avg_win is not None and avg_loss is not None else f"매도 {n}건 · 승률 {win_rate:.0%} · 거래당 {expectancy:+.2f}%")
    out["cause"] = (f"가장 큰 원인: {top}" if top else "뚜렷한 반복 문제를 찾지 못했습니다") + (" (거래가 적어 참고만)" if few else "")
    out["suggested"] = suggest(closed, adds, rules)
    return out


def suggest(orders: Sequence[OrderResult], adds: Sequence[AddEvent], rules: TradeRules) -> dict[str, Any]:
    """Rule values from the owner's own trades (shown with why; applied only when saved)."""
    wins = [o for o in orders if o.net_pnl > 0 and o.mae is not None]
    why: list[str] = []
    s = asdict(rules) | {"saved_at": None}
    if len(wins) >= MIN_TRADES:
        dip = _pct([o.mae for o in wins if o.mae is not None], 0.2)  # 80 % of winners never went lower than this
        stop = max(-12.0, min(-4.0, round(dip - 1.0)))
        s["stop_pct"] = stop
        why.append(f"이긴 거래 {len(wins)}건의 80%는 보유 중 {dip:+.1f}%보다 깊이 빠지지 않았습니다 → 손절 {stop:g}%면 이긴 거래 대부분은 지키면서 큰 손실을 막습니다")
        mfes = [o.mfe for o in wins if o.mfe is not None]
        if mfes:
            tp = max(5.0, min(30.0, round(median(mfes))))
            s["take1_pct"] = tp
            why.append(f"이긴 거래의 보유 중 최고 상승 중앙값 {median(mfes):+.1f}% → 1차 익절 {tp:+g}%")
    else:
        why.append(f"이긴 거래가 {len(wins)}건뿐이라 기본값을 제안합니다(5건 이상이면 내 거래에서 계산)")
    if any(a.below_avg_pct <= -3.0 for a in adds):
        s["add_mode"] = "winners_only"
        why.append("물타기 기록이 있어 추가매수는 수익 중일 때만으로 제안합니다")
    return {"rules": s, "why": why}


# ------------------------------------------------------------------ the mechanical plan of one holding
@dataclass
class HoldingState:
    symbol: str
    currency: str
    quantity: float
    avg_price: float
    price: float | None
    price_at: str | None
    price_source: str | None
    first_qty: float | None  # the first purchase of the current position (adds are sized from it)
    opened: str | None  # when the current position was opened (None: before the fetched records)
    adds_done: int
    sold_since_open: bool  # a partial sale since the position was opened (the first take-profit taken)
    high_since_open: float | None
    app_action: str | None
    app_stop: float | None
    app_target: float | None
    app_thesis_broken: bool = False


def holding_plan(h: HoldingState, r: TradeRules) -> dict[str, Any]:
    avg = h.avg_price
    pct_stop = avg * (1 + r.stop_pct / 100)
    stop, stop_src = pct_stop, f"평단 {r.stop_pct:g}%"
    if r.use_app_stop and h.app_stop is not None and h.app_stop > pct_stop and h.app_stop < (h.price or avg * 10):
        stop, stop_src = h.app_stop, "앱 분석 손절가(평단 % 손절보다 높아 사용)"
    take1 = avg * (1 + r.take1_pct / 100)
    reached = h.sold_since_open or (h.high_since_open is not None and h.high_since_open >= take1)
    trail = h.high_since_open * (1 + r.trail_pct / 100) if reached and h.high_since_open is not None else None
    if trail is not None:
        trail = max(trail, avg)  # once the first target was reached the rest is never given back below cost
    add_level = avg * (1 + r.add_trigger_pct / 100) if r.add_mode == "winners_only" else None
    add_qty = round((h.first_qty or h.quantity) * r.add_fraction, 6)
    plan: dict[str, Any] = {
        "symbol": h.symbol, "currency": h.currency, "quantity": h.quantity, "avg_price": avg, "price": h.price, "price_at": h.price_at,
        "price_source": h.price_source, "pnl_pct": ((h.price / avg - 1) * 100) if h.price and avg else None,
        "stop": round(stop, 4), "stop_source": stop_src, "take1": round(take1, 4), "take1_fraction": r.take1_fraction,
        "take1_done": h.sold_since_open, "trail": round(trail, 4) if trail is not None else None, "high_since_open": h.high_since_open,
        "add_mode": r.add_mode, "add_level": round(add_level, 4) if add_level is not None else None, "add_qty": add_qty,
        "adds_done": h.adds_done, "max_adds": r.max_adds, "opened": h.opened, "app_action": h.app_action, "app_stop": h.app_stop,
        "app_target": h.app_target, "rules_saved": r.saved_at is not None, "notes": [],
    }
    notes = plan["notes"]
    if h.opened is None:
        notes.append("이 포지션을 처음 산 체결이 조회 기간에 없어 추가매수 횟수·고점은 확인한 범위에서만 계산")
    if h.high_since_open is None:
        notes.append("매수 이후 고점 기록이 없어 추적 손절은 계산하지 않음")
    p = h.price
    if p is None:
        plan |= {"action": "NO_PRICE", "action_ko": "가격 확인 불가", "detail": "현재가가 없어 규칙을 적용하지 않았습니다"}
        return plan
    if p <= stop:
        plan |= {"action": "STOP", "action_ko": "손절", "detail": f"현재가 {p:g} ≤ 손절가 {stop:.2f} ({stop_src}) — 규칙상 전량 매도"}
    elif trail is not None and p <= trail:
        plan |= {"action": "TRAIL", "action_ko": "추적 손절", "detail": f"현재가 {p:g} ≤ 고점 {h.high_since_open:g} 대비 {r.trail_pct:g}% ({trail:.2f}) — 규칙상 남은 수량 매도"}
    elif not h.sold_since_open and p >= take1:
        plan |= {"action": "TAKE1", "action_ko": "1차 익절", "detail": f"현재가 {p:g} ≥ 1차 익절가 {take1:.2f} — 규칙상 {r.take1_fraction:.0%} 매도, 나머지는 추적 손절"}
    elif r.add_mode != "none" and h.adds_done < r.max_adds and (add_level is None or p >= add_level) and not h.app_thesis_broken \
            and h.app_action not in ("SELL", "AVOID", "REDUCE") and (r.add_mode == "any" or p > avg):
        lvl = f"평단 +{r.add_trigger_pct:g}% ({add_level:.2f}) 이상" if add_level is not None else "조건 없음"
        plan |= {"action": "ADD", "action_ko": "추가매수 가능", "detail": f"현재가 {p:g}: {lvl} · 추가매수 {h.adds_done}/{r.max_adds}회 → {add_qty:g}주까지 규칙상 가능"}
    else:
        nxt = [f"손절 {stop:.2f} ({(stop / p - 1) * 100:+.1f}%)"]
        if trail is not None:
            nxt.append(f"추적 손절 {trail:.2f} ({(trail / p - 1) * 100:+.1f}%)")
        if not h.sold_since_open:
            nxt.append(f"1차 익절 {take1:.2f} ({(take1 / p - 1) * 100:+.1f}%)")
        if add_level is not None and h.adds_done < r.max_adds and r.add_mode != "none":
            nxt.append(f"추가매수 {add_level:.2f} 이상")
        plan |= {"action": "HOLD", "action_ko": "보유 유지", "detail": "다음 행동 가격: " + " · ".join(nxt)}
        if r.add_mode == "winners_only" and p < avg:
            notes.append("평단 아래 — 규칙상 물타기(추가매수) 하지 않음")
    if h.app_thesis_broken or h.app_action in ("SELL", "AVOID", "REDUCE"):
        notes.append(f"앱 분석 판정 {h.app_action or ''}{' · 투자 논리 훼손' if h.app_thesis_broken else ''} — 규칙과 별개로 점검 필요")
    return plan
