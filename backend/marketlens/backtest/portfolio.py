"""Daily portfolio backtest of the strategies (PREREGISTRATION §19, docs/strategies/STRATEGIES.md).

    python -m marketlens.backtest.portfolio --db backtest.db [--rows rows.db] --out portfolio.json [--trades trades.csv]

Every session: signals at the close (domain/strategies.py — the same code the app runs), entries and rule exits at the
next open, stops inside the day (variant only), dividends on the ex-date, a delisted name at its last close × 0.7.
The account tracks cash, every position, the sector count and the number of positions; money is never assigned twice.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Callable, Iterable, Sequence

import numpy as np

from marketlens.domain import strategies as S

START_CASH = 100_000.0
DELIST_LOSS = 0.30
COST = 0.0015  # a side: commission 0.10 % + spread and slippage 0.05 %
COST_STRESS = 0.0030
SECTOR_MAX = 3
FULL = (date(2017, 1, 6), date(2026, 9, 25))
HALVES = ((date(2017, 1, 6), date(2023, 12, 29)), (date(2024, 1, 2), date(2026, 9, 25)))
STOCK_TYPES = frozenset({"CS", "ADRC", "OS"})
EXCHANGES = frozenset({"XNAS", "XNYS", "XASE"})


@dataclass(frozen=True)
class Sleeve:
    strategy: str
    slots: int
    stop: bool = False
    fundamental: bool = False


@dataclass(frozen=True)
class Variant:
    id: str
    name: str
    sleeves: tuple[Sleeve, ...]


VARIANTS: tuple[Variant, ...] = (
    Variant("A", "A 단독", (Sleeve("A", 10),)),
    Variant("C", "C 단독", (Sleeve("C", 10),)),
    Variant("A+C", "A+C 조합(전략별 5칸)", (Sleeve("A", 5), Sleeve("C", 5))),
    Variant("A+stop", "A + 손절(2×ATR)", (Sleeve("A", 10, stop=True),)),
    Variant("C+stop", "C + 손절(2×ATR)", (Sleeve("C", 10, stop=True),)),
    Variant("A+fund", "A + 재무 필터(≥0.6)", (Sleeve("A", 10, fundamental=True),)),
    Variant("C+fund", "C + 재무 필터(≥0.6)", (Sleeve("C", 10, fundamental=True),)),
)


# ---------------------------------------------------------------------- data
@dataclass
class Sec:
    key: str
    label: str
    sector: str
    days: list[date]
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    ind: dict[str, np.ndarray]
    dividends: dict[date, float] = field(default_factory=dict)  # per share on the newest basis
    index: dict[date, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {d: i for i, d in enumerate(self.days)}


def make_sec(key: str, label: str, sector: str, days: Sequence[date], rows: Sequence[tuple[float, float, float, float, float]],
             splits: Sequence[tuple[date, float]], dividends: Iterable[tuple[date, float]] = ()) -> Sec:
    o, h, lo, c, v, raw, f = S.split_adjust(days, rows, splits)
    ind = S.indicators(o, h, lo, c, v, raw)
    fac = {d: f[i] for i, d in enumerate(days)}
    divs: dict[date, float] = {}
    for d, amt in dividends:
        if amt > 0:
            k = bisect_left(list(days), d)
            factor = fac.get(d, f[min(k, len(days) - 1)] if len(days) else 1.0)
            divs[d] = divs.get(d, 0.0) + amt / factor
    return Sec(key, label, sector, list(days), o, h, lo, c, ind, divs)


def fundamental_lookup(rows_db: str | None) -> Callable[[str, date], float | None] | None:
    """The analysis' fundamental sub-score of a security at its last weekly analysis BEFORE day d (§16 rows)."""
    if not rows_db:
        return None
    import sqlite3

    con = sqlite3.connect(rows_db)
    by: dict[str, tuple[list[date], list[float | None]]] = {}
    for t, key, payload in con.execute("SELECT t, key, payload FROM bt_rows WHERE eligible = 1 ORDER BY t"):
        p = json.loads(payload or "{}")
        comp = (p.get("components") or {}).get("fundamental") or {}
        sub = comp.get("sub") if comp.get("available") else None
        d = date.fromisoformat(str(t)[:10])
        ds, vs = by.setdefault(key, ([], []))
        ds.append(d)
        vs.append(sub)

    def look(key: str, d: date) -> float | None:
        e = by.get(key)
        if e is None:
            return None
        k = bisect_left(e[0], d) - 1  # strictly before d: the week's analysis is public from the next session
        return e[1][k] if k >= 0 else None

    return look


def load(db: str) -> tuple[dict[str, Sec], Sec, list[date]]:
    from marketlens.backtest.schema import bt_engine
    from marketlens.backtest.store import BacktestData
    from marketlens.infrastructure.db.session import make_session_factory

    eng = bt_engine(db)
    data = BacktestData(eng, make_session_factory(eng))
    secs: dict[str, Sec] = {}
    spy: Sec | None = None
    for ln in data.lineages:
        if len(ln.days) < 30:
            continue
        labels = set(ln.labels)
        is_spy = "SPY" in labels and (ln.type or "") not in STOCK_TYPES
        if not is_spy and not ((ln.type or "") in STOCK_TYPES and (ln.exchange or "") in EXCHANGES):
            continue
        sector = data.profiles.get(ln.cik, ("Unknown",))[0] if ln.cik is not None else "Unknown"
        s = make_sec(ln.key, ln.labels[-1], sector or "Unknown", ln.days, ln.rows,
                     [(sp.execution_date, sp.split_to / sp.split_from) for sp in ln.splits if sp.split_from > 0], ln.dividends)
        if is_spy:
            if spy is None or len(s.days) > len(spy.days):
                spy = s
        else:
            secs[ln.key] = s
    if spy is None:
        raise SystemExit("SPY bars not found in the backtest database")
    return secs, spy, list(spy.days)


# ---------------------------------------------------------------------- signals (once per strategy)
def spy_up_map(spy: Sec) -> dict[date, bool]:
    c, m = spy.c, S.sma(spy.c, 200)
    return {d: bool(c[i] > m[i]) for i, d in enumerate(spy.days)}


def candidates(strategy: str, secs: dict[str, Sec], up: dict[date, bool]) -> dict[date, list[tuple[float, str]]]:
    out: dict[date, list[tuple[float, str]]] = {}
    for k, s in secs.items():
        spy_up = np.array([up.get(d, False) for d in s.days]) if strategy == "C" else None
        sig = S.signals(strategy, s.ind, spy_up)
        pr = S.priority(strategy, s.ind)
        for i in np.flatnonzero(sig):
            out.setdefault(s.days[i], []).append((float(pr[i]) if pr[i] == pr[i] else 0.0, k))
    for lst in out.values():
        lst.sort(key=lambda x: (-x[0], x[1]))
    return out


# ---------------------------------------------------------------------- the account
@dataclass
class Pos:
    key: str
    strategy: str
    sleeve: int
    entry_day: date
    entry_px: float
    qty: float
    stop: float | None
    sector: str
    held: int = 0
    exit_reason: str | None = None  # decided at a close, filled at the next traded open
    dividends: float = 0.0


@dataclass
class Trade:
    key: str
    ticker: str
    strategy: str
    sector: str
    entry_day: date
    exit_day: date
    entry_px: float
    exit_px: float
    qty: float
    pnl: float  # dollars, after both costs, with dividends received
    ret: float  # net return of the trade
    sessions: int
    reason: str


def simulate(variant: Variant, secs: dict[str, Sec], spy: Sec, calendar: Sequence[date], cands: dict[str, dict[date, list[tuple[float, str]]]],
             cost: float = COST, start: date | None = None, end: date | None = None,
             fund: Callable[[str, date], float | None] | None = None) -> dict[str, Any]:
    cal = [d for d in calendar if (start is None or d >= start) and (end is None or d <= end)]
    cash = START_CASH
    pos: dict[str, Pos] = {}
    pending: list[tuple[int, str, date]] = []  # (sleeve, key, signal day) to buy at the next open, in allocation order
    trades: list[Trade] = []
    equity: list[tuple[date, float]] = []
    invested: list[float] = []
    missed = delayed = 0
    last_close: dict[str, float] = {}
    eq_prev = START_CASH

    def sell(p: Pos, d: date, px: float, reason: str) -> None:
        nonlocal cash
        cash += p.qty * px * (1 - cost)
        pnl = p.qty * px * (1 - cost) - p.qty * p.entry_px * (1 + cost) + p.dividends
        trades.append(Trade(p.key, secs[p.key].label, p.strategy, p.sector, p.entry_day, d, p.entry_px, px, p.qty, pnl,
                            pnl / (p.qty * p.entry_px * (1 + cost)), p.held, reason))

    for d in cal:
        # 1) the open: exits decided at an earlier close (a name not traded today waits for its next traded open)
        for k in list(pos):
            p = pos[k]
            s = secs[k]
            if p.exit_reason is None:
                continue
            i = s.index.get(d)
            if i is None:
                delayed += 1
                continue
            sell(p, d, float(s.o[i]), p.exit_reason)
            del pos[k]
        # entries: in the fixed order; a name not traded today is cancelled (recorded)
        sectors: dict[str, int] = {}
        for p in pos.values():
            sectors[p.sector] = sectors.get(p.sector, 0) + 1
        used = [sum(1 for p in pos.values() if p.sleeve == j) for j in range(len(variant.sleeves))]
        for j, k, sig_day in pending:
            sl = variant.sleeves[j]
            if k in pos or used[j] >= sl.slots or len(pos) >= sum(x.slots for x in variant.sleeves):
                continue
            s = secs[k]
            if sectors.get(s.sector, 0) >= SECTOR_MAX:
                continue
            i = s.index.get(d)
            if i is None or not s.o[i] > 0:
                missed += 1
                continue
            px = float(s.o[i])
            budget = min(eq_prev * 0.10, cash / (1 + cost))
            if budget < 100:  # no money left today
                break
            qty = budget / px
            cash -= qty * px * (1 + cost)
            si = s.index[sig_day]
            a = s.ind["atr14"][si]
            stop = px - S.STOP_ATR * a if sl.stop and a == a else None
            pos[k] = Pos(k, sl.strategy, j, d, px, qty, stop, s.sector)
            used[j] += 1
            sectors[s.sector] = sectors.get(s.sector, 0) + 1
        pending = []
        # 2) inside the day: stops (variant), the entry day included; a gap below fills at the open
        for k in list(pos):
            p = pos[k]
            if p.stop is None or p.exit_reason is not None:
                continue
            s = secs[k]
            i = s.index.get(d)
            if i is not None and s.lo[i] <= p.stop:
                sell(p, d, float(min(s.o[i], p.stop)), "stop")
                del pos[k]
        # 3) the close: dividends (held before the ex-date), marks, exits and entries for the next open
        value = 0.0
        for k in list(pos):
            p = pos[k]
            s = secs[k]
            i = s.index.get(d)
            if i is None:
                if s.days[-1] < d and (d - s.days[-1]).days > 7:  # stopped trading: delisted
                    sell(p, d, last_close.get(k, p.entry_px) * (1 - DELIST_LOSS), "delisted")
                    del pos[k]
                    continue
                value += p.qty * last_close.get(k, p.entry_px)
                continue
            amt = s.dividends.get(d)
            if amt and p.entry_day < d:
                cash += p.qty * amt
                p.dividends += p.qty * amt
            last_close[k] = float(s.c[i])
            p.held += 1
            if p.exit_reason is None:
                why = S.exit_due(p.strategy, s.ind, i, p.held)
                if why:
                    p.exit_reason = why
            value += p.qty * last_close[k]
        eq = cash + value
        equity.append((d, eq))
        invested.append(value / eq if eq > 0 else 0.0)
        eq_prev = eq
        for j, sl in enumerate(variant.sleeves):
            free = sl.slots - sum(1 for p in pos.values() if p.sleeve == j and p.exit_reason is None)
            if free <= 0:
                continue
            got = 0
            for _pr, k in cands[sl.strategy].get(d, ()):
                if k in pos or any(x[1] == k for x in pending):
                    continue
                if sl.fundamental:
                    v = fund(k, d) if fund else None
                    if v is None or v < S.FUNDAMENTAL_MIN:
                        continue
                pending.append((j, k, d))
                got += 1
                if got >= free + 5:  # a few spares for names that do not open tomorrow or hit the sector limit
                    break
    return {"equity": equity, "invested": invested, "trades": trades, "missed": missed, "delayed": delayed,
            "open": [(p.key, p.sector, p.entry_day, p.strategy) for p in pos.values()]}


# ---------------------------------------------------------------------- measures
def spy_total_return(spy: Sec, start: date, end: date) -> list[tuple[date, float]]:
    """SPY bought at the first close of the window, dividends reinvested at the ex-date close."""
    out: list[tuple[date, float]] = []
    units = None
    for i, d in enumerate(spy.days):
        if d < start or d > end:
            continue
        c = float(spy.c[i])
        if units is None:
            units = START_CASH / c
        amt = spy.dividends.get(d)
        if amt and out:
            units += units * amt / c
        out.append((d, units * c))
    return out


def _cagr(eq: Sequence[tuple[date, float]]) -> float | None:
    if len(eq) < 2 or eq[0][1] <= 0:
        return None
    years = (eq[-1][0] - eq[0][0]).days / 365.25
    return (eq[-1][1] / eq[0][1]) ** (1 / years) - 1 if years > 0 and eq[-1][1] > 0 else -1.0


def series_stats(eq: Sequence[tuple[date, float]]) -> dict[str, Any]:
    if len(eq) < 2:
        return {}
    v = np.array([x for _, x in eq], dtype=float)
    r = v[1:] / v[:-1] - 1
    sd = float(np.std(r))
    peak = np.maximum.accumulate(v)
    return {"start": eq[0][0].isoformat(), "end": eq[-1][0].isoformat(), "cagr": _cagr(eq), "total_return": float(v[-1] / v[0] - 1),
            "volatility": sd * math.sqrt(252), "sharpe": float(np.mean(r) / sd * math.sqrt(252)) if sd > 0 else None,
            "max_drawdown": float(np.min(v / peak - 1))}


def matched_spy(eq_dates: Sequence[date], invested: Sequence[float], spy_tr: Sequence[tuple[date, float]]) -> list[tuple[date, float]]:
    """SPY with the strategy's exposure: each day the invested fraction at the previous close × SPY's total return."""
    tr = dict(spy_tr)
    out = [(eq_dates[0], START_CASH)]
    v = START_CASH
    for k in range(1, len(eq_dates)):
        a, b = tr.get(eq_dates[k - 1]), tr.get(eq_dates[k])
        r = (b / a - 1) if a and b else 0.0
        v *= 1 + invested[k - 1] * r
        out.append((eq_dates[k], v))
    return out


def trade_stats(trades: Sequence[Trade], equity: Sequence[tuple[date, float]]) -> dict[str, Any]:
    n = len(trades)
    if not n:
        return {"trades": 0}
    rets = np.array([t.ret for t in trades])
    pnl = np.array([t.pnl for t in trades])
    wins, losses = rets[rets > 0], rets[rets <= 0]
    years = (equity[-1][0] - equity[0][0]).days / 365.25 if len(equity) > 1 else 1.0
    avg_eq = float(np.mean([x for _, x in equity]))
    bought = sum(t.qty * t.entry_px for t in trades)
    total = float(pnl.sum())
    top10 = float(np.sort(pnl)[::-1][:10].sum())
    by_ticker: dict[str, float] = {}
    for t in trades:
        by_ticker[t.ticker] = by_ticker.get(t.ticker, 0.0) + t.pnl
    top5 = sorted(by_ticker.items(), key=lambda x: -x[1])[:5]
    return {
        "trades": n, "trades_per_year": n / years, "win_rate": float(np.mean(rets > 0)),
        "avg_win": float(wins.mean()) if len(wins) else None, "avg_loss": float(losses.mean()) if len(losses) else None,
        "expectancy": float(rets.mean()), "avg_sessions": float(np.mean([t.sessions for t in trades])),
        "turnover_per_year": bought / avg_eq / years if avg_eq > 0 and years > 0 else None,
        "net_pnl": total, "top10_trade_share": top10 / total if total > 0 else None,
        "top5_tickers": [{"ticker": k, "pnl": round(v, 2), "share": (v / total if total > 0 else None)} for k, v in top5],
        "exit_reasons": {r: sum(1 for t in trades if t.reason == r) for r in sorted({t.reason for t in trades})},
    }


def by_year(eq: Sequence[tuple[date, float]]) -> dict[str, float]:
    out: dict[str, float] = {}
    first: dict[int, float] = {}
    last: dict[int, float] = {}
    prev = None
    for d, v in eq:
        if d.year not in first:
            first[d.year] = prev if prev is not None else v
        last[d.year] = v
        prev = v
    for y in sorted(first):
        out[str(y)] = last[y] / first[y] - 1
    return out


def by_regime(eq: Sequence[tuple[date, float]], up: dict[date, bool]) -> dict[str, Any]:
    """Annualised return of the days after SPY closed above / below its 200-day line."""
    acc = {"spy_above_200": [1.0, 0], "spy_below_200": [1.0, 0]}
    for k in range(1, len(eq)):
        r = eq[k][1] / eq[k - 1][1]
        g = "spy_above_200" if up.get(eq[k - 1][0], False) else "spy_below_200"
        acc[g][0] *= r
        acc[g][1] += 1
    return {g: {"days": n, "annualised": (m ** (252 / n) - 1) if n else None} for g, (m, n) in acc.items()}


def verdict(full: dict[str, Any], halves: list[dict[str, Any]], stress: dict[str, Any], spy_tr: dict[str, Any], matched: dict[str, Any]) -> dict[str, Any]:
    """§19's six conditions (all, base cost)."""
    t = full["trades"]
    checks = {
        "cagr_beats_exposure_matched_spy": bool(full["cagr"] is not None and matched.get("cagr") is not None and full["cagr"] > matched["cagr"]),
        "sharpe_beats_spy_total_return": bool(full.get("sharpe") is not None and spy_tr.get("sharpe") is not None and full["sharpe"] > spy_tr["sharpe"]),
        "both_halves_positive": all(h.get("cagr") is not None and h["cagr"] > 0 for h in halves),
        "stress_expectancy_positive": bool((stress["trades"].get("expectancy") or -1) > 0),
        "top10_trades_under_half": bool(t.get("top10_trade_share") is not None and t["top10_trade_share"] < 0.5),
        "at_least_100_trades": bool(t.get("trades", 0) >= 100),
    }
    return {"checks": checks, "passed": all(checks.values())}


def run_all(secs: dict[str, Sec], spy: Sec, cal: list[date], fund: Callable[[str, date], float | None] | None,
            variants: Sequence[Variant] = VARIANTS, log: Callable[[str], None] = print) -> tuple[dict[str, Any], list[tuple[str, Trade]]]:
    up = spy_up_map(spy)
    cands = {st: candidates(st, secs, up) for st in ("A", "C")}
    spy_full = spy_total_return(spy, *FULL)
    spy_stats = series_stats(spy_full)
    out: dict[str, Any] = {"benchmarks": {"spy_total_return": spy_stats | {"by_year": by_year(spy_full), "by_regime": by_regime(spy_full, up),
                                                                         "halves": [series_stats(spy_total_return(spy, *h)) for h in HALVES]}},
                           "variants": {}}
    all_trades: list[tuple[str, Trade]] = []
    for v in variants:
        if any(sl.fundamental for sl in v.sleeves) and fund is None:
            out["variants"][v.id] = {"name": v.name, "skipped": "재무 점수 자료(rows.db) 없음 — 계산하지 않음"}
            continue
        sim = simulate(v, secs, spy, cal, cands, COST, *FULL, fund=fund)
        eq = sim["equity"]
        full = series_stats(eq) | {"trades": trade_stats(sim["trades"], eq), "exposure": float(np.mean(sim["invested"])),
                                   "missed_entries": sim["missed"], "delayed_exits": sim["delayed"], "by_year": by_year(eq),
                                   "by_regime": by_regime(eq, up)}
        matched = series_stats(matched_spy([d for d, _ in eq], sim["invested"], spy_full))
        st = simulate(v, secs, spy, cal, cands, COST_STRESS, *FULL, fund=fund)
        stress = series_stats(st["equity"]) | {"trades": trade_stats(st["trades"], st["equity"])}
        halves = []
        for a, b in HALVES:
            h = simulate(v, secs, spy, cal, cands, COST, a, b, fund=fund)
            halves.append(series_stats(h["equity"]) | {"trades": trade_stats(h["trades"], h["equity"]), "exposure": float(np.mean(h["invested"]))})
        res = {"name": v.name, "full": full, "exposure_matched_spy": matched, "stress_cost": stress, "halves": halves}
        res["verdict"] = verdict(full, halves, stress, spy_stats, matched)
        out["variants"][v.id] = res
        all_trades += [(v.id, t) for t in sim["trades"]]
        brief = {k: full.get(k) for k in ("cagr", "sharpe", "max_drawdown", "exposure")} | {"trades": full["trades"].get("trades"), "matched_cagr": matched.get("cagr"), "passed": res["verdict"]["passed"]}
        log(f"{v.id}: {json.dumps(brief, default=str)}")
    return out, all_trades


def _sha(path: str | None) -> str | None:
    if not path or not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--rows", default=None, help="rows.db of the §16 run (the fundamental filter); without it the +재무 variants are skipped")
    ap.add_argument("--out", required=True)
    ap.add_argument("--trades", default=None)
    a = ap.parse_args(argv)
    secs, spy, cal = load(a.db)
    print(f"securities {len(secs)} · sessions {len(cal)} ({cal[0]} ~ {cal[-1]})", flush=True)
    res, trades = run_all(secs, spy, cal, fundamental_lookup(a.rows))
    res["source"] = {"commit": os.environ.get("GITHUB_SHA"), "workflow_run": os.environ.get("GITHUB_RUN_ID"), "data_sha256": _sha(a.db),
                     "rows_sha256": _sha(a.rows), "finished_at": datetime.utcnow().isoformat() + "Z", "prereg": "PREREGISTRATION §19",
                     "strategies": {k: v.version for k, v in S.STRATEGIES.items()}, "cost": COST, "cost_stress": COST_STRESS,
                     "securities": len(secs), "sessions": len(cal)}
    res["comparison_note"] = "기존 MarketLens 전략(§16)은 주간 엔진 결과를 인용: 연 −0.02%, 최대 낙폭 −14.0%, 샤프 −0.36, 1,623회 (같은 자료, 다른 엔진)"
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    if a.trades:
        with open(a.trades, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["variant", "strategy", "ticker", "sector", "entry_day", "exit_day", "entry_px", "exit_px", "qty", "pnl", "ret", "sessions", "reason"])
            for vid, t in trades:
                w.writerow([vid, t.strategy, t.ticker, t.sector, t.entry_day, t.exit_day, round(t.entry_px, 4), round(t.exit_px, 4), round(t.qty, 4),
                            round(t.pnl, 2), round(t.ret, 5), t.sessions, t.reason])
    print("=== verdicts", json.dumps({k: (v.get("verdict") or {}).get("passed") for k, v in res["variants"].items()}), flush=True)


if __name__ == "__main__":
    main()
