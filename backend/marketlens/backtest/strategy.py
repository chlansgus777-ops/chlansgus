"""The app-rule strategy: the app's paper account (domain/paper.simulate_account) on the backtest's recommendations.

$100k start, the app's position sizing and limits, close-based stops (a close at/below the stop exits at the next
open), targets on the session high, time exit after 60 sessions, exits on a later non-bullish recommendation. Costs:
commission as slippage + the ADV half-spread, at k = 0×/1×/2×. Cash earns the 3-month T-bill rate (FRED DTB3, the
value known on each day). Delisting: a position in a security that stopped trading is sold at the open of a terminal
session after its last bar priced at last close × (1 − loss), loss ∈ {0, 30 %, 55 %} — an ASSUMPTION of the report,
flagged as such, never a price anyone saw.
"""

from __future__ import annotations

import math
from bisect import bisect_right
from dataclasses import replace
from datetime import date, datetime, time
from typing import Any, Callable, Sequence

from marketlens.backtest.engine import Signal, account_inputs, paper_config
from marketlens.backtest.metrics import block_bootstrap_ci
from marketlens.domain.enums import ExitReason
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import NY, next_trading_day
from marketlens.domain.paper import AccountResult, PaperConfig, simulate_account


def with_delisting(bars: dict[str, list[Bar]], items: list[Any], data_end: date, loss: float, stale_days: int = 7) -> tuple[dict[str, list[Bar]], list[Any]]:
    """Terminal session + exit event for every security whose bars end before the data end (a delisting)."""
    out = dict(bars)
    ended: dict[str, date] = {}
    for k, bs in bars.items():
        if bs and (data_end - bs[-1].day).days > stale_days:
            last = bs[-1]
            px = last.close * (1 - loss)
            term = next_trading_day(last.day)
            out[k] = list(bs) + [Bar(term, px, px, px, px, 0.0)]
            ended[k] = last.day
    new_items = []
    for it in items:
        d = ended.get(it.signal.ticker)
        if d is not None and it.signal.recommended_at < datetime.combine(d, time(16, 0), tzinfo=NY):
            # the earliest exit wins (simulate fills the first due one); the delisting exit is at the last close
            it = replace(it, exit_events=tuple(sorted(tuple(it.exit_events) + ((datetime.combine(d, time(16, 0), tzinfo=NY), ExitReason.RECOMMENDATION_DOWNGRADE),))))
        new_items.append(it)
    return out, new_items


def cash_series(acct: AccountResult, bars: dict[str, list[Bar]]) -> dict[date, float]:
    """Cash at each session's close = equity − open positions at that close (the account's own bookkeeping)."""
    closes = {k: {b.day: b.close for b in bs} for k, bs in bars.items()}
    out = {}
    last_px: dict[str, float] = {}
    for d, eq in acct.equity:
        for k, c in closes.items():
            if d in c:
                last_px[k] = c[d]
        val = 0.0
        for _key, r in acct.trades:
            if r.entry is None or r.entry.day > d:
                continue
            q = r.entry.quantity - sum(e.quantity for e in r.exits if e.day <= d)
            if q > 1e-9:
                val += q * last_px.get(r.signal.ticker, r.entry.price)
        out[d] = eq - val
    return out


def with_interest(acct: AccountResult, cash: dict[date, float], dtb3: list[tuple[date, float]]) -> list[tuple[date, float]]:
    """Equity plus T-bill interest on the cash balance (interest accrues on the previous close's cash, compounding)."""
    curve = []
    earned = 0.0
    prev_d = None
    for d, eq in acct.equity:
        if prev_d is not None:
            i = bisect_right(dtb3, (prev_d, float("inf"))) - 1
            y = dtb3[i][1] / 100 if i >= 0 else 0.0
            f = y * (d - prev_d).days / 365
            earned = earned * (1 + f) + (cash[prev_d] + 0.0) * f
        curve.append((d, eq + earned))
        prev_d = d
    return curve


def curve_stats(curve: Sequence[tuple[date, float]], spy: dict[date, float], dtb3: list[tuple[date, float]], start_capital: float) -> dict[str, Any]:
    if len(curve) < 2:
        return {}
    days = [d for d, _ in curve]
    vals = [v for _, v in curve]
    years = (days[-1] - days[0]).days / 365.25
    cagr = (vals[-1] / start_capital) ** (1 / years) - 1 if years > 0 and vals[-1] > 0 else None
    rets = [vals[i] / vals[i - 1] - 1 for i in range(1, len(vals))]
    mu = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mu) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
    rf = []
    for d in days[1:]:
        i = bisect_right(dtb3, (d, float("inf"))) - 1
        rf.append((dtb3[i][1] / 100 / 252) if i >= 0 else 0.0)
    ex = [r - f for r, f in zip(rets, rf)]
    sharpe = (sum(ex) / len(ex)) / sd * math.sqrt(252) if sd > 0 else None
    peak, mdd = vals[0], 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    # weekly excess over SPY total return (Fridays / last session of each week)
    weekly: list[float] = []
    by_week: dict[tuple[int, int], date] = {}
    for d in days:
        by_week[d.isocalendar()[:2]] = d
    wk = sorted(by_week.values())
    val_of = dict(curve)
    for a, b in zip(wk, wk[1:]):
        if a in spy and b in spy:
            weekly.append((val_of[b] / val_of[a]) - (spy[b] / spy[a]))
    lo, hi = block_bootstrap_ci(weekly)
    spy_days = [d for d in days if d in spy]
    spy_cagr = (spy[spy_days[-1]] / spy[spy_days[0]]) ** (1 / years) - 1 if years > 0 and len(spy_days) > 1 else None
    return {
        "start": days[0].isoformat(), "end": days[-1].isoformat(), "final_equity": round(vals[-1], 2), "cagr": cagr, "spy_cagr": spy_cagr,
        "excess_cagr": (cagr - spy_cagr) if cagr is not None and spy_cagr is not None else None,
        "weekly_excess_mean": (sum(weekly) / len(weekly)) if weekly else None, "weekly_excess_ci90": [lo, hi], "weeks": len(weekly),
        "volatility": sd * math.sqrt(252), "sharpe": sharpe, "max_drawdown": mdd,
    }


def trade_stats(acct: AccountResult, cost_of: Callable[[str], float], start_capital: float, curve: Sequence[tuple[date, float]]) -> dict[str, Any]:
    traded = 0.0
    costs = 0.0
    holds = []
    for _k, r in acct.trades:
        if r.entry is None:
            continue
        n_in = r.entry.price * r.entry.quantity
        traded += n_in
        costs += n_in * cost_of(_k)
        for e in r.exits:
            traded += e.price * e.quantity
            costs += e.price * e.quantity * cost_of(_k)
        if r.holding_days is not None:
            holds.append(r.holding_days)
    years = ((curve[-1][0] - curve[0][0]).days / 365.25) if len(curve) > 1 else 0
    avg_eq = sum(v for _, v in curve) / len(curve) if curve else start_capital
    return {"trades": len([1 for _k, r in acct.trades if r.entry]), "skipped": len(acct.skipped),
            "avg_holding_sessions": (sum(holds) / len(holds)) if holds else None,
            "turnover_per_year": (traded / 2 / avg_eq / years) if years > 0 and avg_eq > 0 else None,
            "costs_paid": round(costs, 2), "stale_marks": list(acct.stale_marks)}


def run_strategy(signals: Sequence[Signal], later: dict[str, list[tuple[datetime, str, bool]]], split_of: Callable[[str], Any],
                 bars: dict[str, list[Bar]], base: PaperConfig, data_end: date, k: float, loss: float,
                 dtb3: list[tuple[date, float]], spy_tr: dict[date, float]) -> dict[str, Any]:
    from marketlens.backtest.engine import half_spread, COMMISSION_ONE_WAY

    items = account_inputs(signals, later, split_of, k)
    b2, items = with_delisting(bars, items, data_end, loss)
    acct = simulate_account(items, b2, paper_config(base, k), data_end)
    cash = cash_series(acct, b2)
    curve = with_interest(acct, cash, dtb3)
    by_key = {f"{i:07d}": s for i, s in enumerate(signals)}
    stats = curve_stats(curve, spy_tr, dtb3, base.starting_capital)
    stats.update(trade_stats(acct, lambda key: k * (COMMISSION_ONE_WAY + half_spread(by_key[key].adv20)), base.starting_capital, curve))
    stats["equity_weekly"] = [(d.isoformat(), round(v, 2)) for d, v in curve[::5]]
    return stats


def ended_before(bars: dict[str, list[Bar]], data_end: date, days: int = 7) -> set[str]:
    return {k for k, bs in bars.items() if bs and (data_end - bs[-1].day).days > days}

