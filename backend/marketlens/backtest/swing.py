"""Short-term price strategies of PREREGISTRATION §17 (owner 2026-10-06: "오르는 추세일 때 들어가 조금 먹고 나오기",
"많이 떨어졌을 때 바닥에서 반등분 먹기") — measured on the same backtest database, apart from the app's score.

    python -m marketlens.backtest.swing --db backtest.db --out swing.json

Rules (fixed in §17 before this code was written and before any result):
- signals use the bars through a session's close only; entries and rule exits fill at the NEXT session's open;
  a stop fills at the stop (or the open below it), a target at the target (or the open above it);
- $100k, at most 10 positions of 10 % of equity at entry, no second position in a held name, costs 0.1 % a side
  (0.2 % as a sensitivity), no interest on cash, no dividends; a security that stops trading is sold at its last
  close × 0.7;
- prices are as traded, split-adjusted by every split (all rules are ratios, so a later split changes no signal);
  the $5 price floor reads the price as traded that day.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable, Sequence

import numpy as np

STRATEGIES = ("A", "B1", "B2", "C")
NAMES = {"A": "상승 추세 눌림목", "B1": "급락 반등(추세 위)", "B2": "급락 반등(추세 무관)", "C": "돌파 추세 추종"}
START_CASH = 100_000.0
SLOTS = 10
DELIST_LOSS = 0.30
TRAIN = (date(2017, 1, 6), date(2023, 10, 20))
HOLDOUT = (date(2024, 1, 19), date(2026, 9, 25))
STOCK_TYPES = frozenset({"CS", "ADRC", "OS"})
EXCHANGES = frozenset({"XNAS", "XNYS", "XASE"})


# ---------------------------------------------------------------------- indicators (pure, numpy)
def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(x, 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder's smoothing (the RSI / ATR average): the first value is the simple mean of the first n, then
    avg = (prev × (n − 1) + x) / n — an exponential average with alpha 1/n seeded by that mean."""
    import pandas as pd

    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    seed = np.array(x, dtype=float)
    seed[: n - 1] = np.nan
    seed[n - 1] = np.mean(x[:n])
    out[:] = pd.Series(seed).ewm(alpha=1.0 / n, adjust=False, ignore_na=False).mean().to_numpy()
    out[: n - 1] = np.nan
    return out


def rsi(close: np.ndarray, n: int = 2) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return out
    d = np.diff(close)
    up, dn = wilder(np.clip(d, 0, None), n), wilder(np.clip(-d, 0, None), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(dn == 0, 100.0, 100.0 - 100.0 / (1.0 + up / np.where(dn == 0, 1, dn)))
    out[1:] = r
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    prev = np.insert(close[:-1], 0, close[0])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return wilder(tr, n)


def _prior_window(x: np.ndarray, n: int, fn: Callable[..., np.ndarray]) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) > n:
        win = np.lib.stride_tricks.sliding_window_view(x, n)[:-1]  # windows ending the day before
        out[n:] = fn(win, axis=1)
    return out


def rolling_max_prior(x: np.ndarray, n: int) -> np.ndarray:
    """The highest of the n values BEFORE each index (the breakout reference excludes today)."""
    return _prior_window(x, n, np.max)


def rolling_min_prior(x: np.ndarray, n: int) -> np.ndarray:
    return _prior_window(x, n, np.min)


# ---------------------------------------------------------------------- one security
@dataclass
class Series:
    key: str
    days: list[date]
    o: np.ndarray
    h: np.ndarray
    lo: np.ndarray
    c: np.ndarray
    v: np.ndarray
    raw_close: np.ndarray  # as traded (the $5 floor)
    ind: dict[str, np.ndarray] = field(default_factory=dict)
    index: dict[date, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {d: i for i, d in enumerate(self.days)}


def adjusted(days: Sequence[date], rows: Sequence[tuple[float, float, float, float, float]], splits: Iterable[tuple[date, float]]) -> tuple[np.ndarray, ...]:
    """OHLC divided (and volume multiplied) by the share ratio of every later split: the series on the newest basis."""
    a = np.array(rows, dtype=float).reshape(-1, 5)
    f = np.ones(len(days))
    dd = np.array([d.toordinal() for d in days])
    for ex, ratio in splits:
        if ratio > 0 and ratio != 1:
            f[dd < ex.toordinal()] *= ratio
    return a[:, 0] / f, a[:, 1] / f, a[:, 2] / f, a[:, 3] / f, a[:, 4] * f, a[:, 3]


def indicators(s: Series) -> None:
    c = s.c
    s.ind = {
        "sma5": sma(c, 5), "sma50": sma(c, 50), "sma200": sma(c, 200), "rsi2": rsi(c, 2), "atr14": atr(s.h, s.lo, c, 14),
        "hi20": rolling_max_prior(c, 20), "lo10": rolling_min_prior(c, 10), "vol20": sma(s.v, 20),
        "dv20": sma(s.c * s.v, 20),  # price × shares: the split factor cancels
    }
    s.ind["sma50_10"] = np.concatenate([np.full(10, np.nan), s.ind["sma50"][:-10]]) if len(c) > 10 else np.full(len(c), np.nan)
    s.ind["ret5"] = np.concatenate([np.full(5, np.nan), c[5:] / c[:-5] - 1]) if len(c) > 5 else np.full(len(c), np.nan)


def signals(strategy: str, s: Series, spy_up: dict[date, bool]) -> np.ndarray:
    """The priority (higher first) of a buy signal at each close, NaN = none. §17's table, literally."""
    g = s.ind
    c = s.c
    up = np.array([spy_up.get(d, False) for d in s.days])
    with np.errstate(invalid="ignore"):
        base = (np.arange(len(c)) >= 252) & (s.raw_close >= 5.0) & (g["dv20"] >= (50e6 if strategy in ("B1", "B2") else 20e6))
        if strategy == "A":
            ok = base & up & (c > g["sma200"]) & (g["sma50"] > g["sma50_10"]) & (g["rsi2"] <= 10)
            pr = -g["rsi2"]
        elif strategy in ("B1", "B2"):
            ok = base & (g["ret5"] <= -0.08) & (g["rsi2"] <= 5)
            if strategy == "B1":
                ok &= c > g["sma200"]
            pr = -g["ret5"]
        elif strategy == "C":
            v20 = g["vol20"]
            ok = base & up & (c > g["hi20"]) & (v20 > 0) & (s.v >= 1.5 * v20) & (c > g["sma50"])
            pr = s.v / np.where(v20 > 0, v20, 1)
        else:
            raise ValueError(strategy)
    return np.where(ok, pr, np.nan)


MAX_HOLD = {"A": 10, "B1": 10, "B2": 10, "C": 40}
TARGET = {"B1": 0.08, "B2": 0.08}


def rule_exit(strategy: str, s: Series, i: int) -> bool:
    """A sell decided at the close of index i (filled at the next open)."""
    g = s.ind
    if strategy in ("A", "B1", "B2"):
        return bool(s.c[i] > g["sma5"][i])
    return bool(s.c[i] < g["lo10"][i])  # C


# ---------------------------------------------------------------------- the account
@dataclass
class Position:
    key: str
    entry_day: date
    entry_px: float
    qty: float
    stop: float
    target: float | None
    held: int = 0  # sessions held (closes seen)
    pending_exit: bool = False


@dataclass
class Trade:
    key: str
    entry_day: date
    exit_day: date
    entry_px: float
    exit_px: float
    ret: float
    sessions: int
    reason: str


def candidates(strategy: str, series: dict[str, Series], spy: Series) -> dict[date, list[tuple[float, str]]]:
    """Every buy signal of a strategy by session, best first (computed once per strategy)."""
    spy_up = {d: bool(spy.c[i] > spy.ind["sma200"][i]) for d, i in spy.index.items()}
    out: dict[date, list[tuple[float, str]]] = {}
    for k, s in series.items():
        pr = signals(strategy, s, spy_up)
        for i in np.flatnonzero(~np.isnan(pr)):
            out.setdefault(s.days[i], []).append((float(pr[i]), k))
    for lst in out.values():
        lst.sort(key=lambda x: (-x[0], x[1]))
    return out


def simulate(strategy: str, series: dict[str, Series], calendar: list[date], spy: Series, cost: float = 0.001,
             start: date | None = None, end: date | None = None, cands: dict[date, list[tuple[float, str]]] | None = None) -> dict[str, Any]:
    """One strategy over [start, end] of the calendar. Pure: the same inputs give the same result."""
    if cands is None:
        cands = candidates(strategy, series, spy)
    cal = [d for d in calendar if (start is None or d >= start) and (end is None or d <= end)]
    cash = START_CASH
    pos: dict[str, Position] = {}
    pending: list[str] = []  # keys to buy at the next open
    trades: list[Trade] = []
    equity: list[tuple[date, float]] = []
    exposure_days = 0
    last_close: dict[str, float] = {}

    def close_trade(p: Position, d: date, px: float, reason: str) -> None:
        nonlocal cash
        cash += p.qty * px * (1 - cost)
        trades.append(Trade(p.key, p.entry_day, d, p.entry_px, px, (px * (1 - cost)) / (p.entry_px * (1 + cost)) - 1, p.held, reason))

    for d in cal:
        # 1) the open: exits decided last close, then entries
        for k in list(pos):
            p = pos[k]
            s = series[k]
            i = s.index.get(d)
            if i is None:
                continue
            if p.pending_exit:
                close_trade(p, d, float(s.o[i]), "rule" if p.held < MAX_HOLD[strategy] else "time")
                del pos[k]
        eq_open = cash + sum(p.qty * last_close.get(k, p.entry_px) for k, p in pos.items())
        for k in pending:
            if len(pos) >= SLOTS or k in pos:
                continue
            s = series[k]
            i = s.index.get(d)
            if i is None or not s.o[i] > 0:
                continue
            px = float(s.o[i])
            budget = min(eq_open / SLOTS, cash / (1 + cost))
            if budget <= 0:
                break
            qty = budget / px
            cash -= qty * px * (1 + cost)
            a = s.ind["atr14"][i - 1] if i > 0 else float("nan")
            stop = px - 2 * a if a == a else px * 0.9
            tgt = px * (1 + TARGET[strategy]) if strategy in TARGET else None
            pos[k] = Position(k, d, px, qty, stop, tgt)
        pending = []
        # 2) the session: stops and targets inside the day
        for k in list(pos):
            p = pos[k]
            s = series[k]
            i = s.index.get(d)
            if i is None:
                continue
            if s.lo[i] <= p.stop:
                close_trade(p, d, float(min(s.o[i], p.stop)), "stop")
                del pos[k]
                continue
            if p.target is not None and s.h[i] >= p.target:
                close_trade(p, d, float(max(s.o[i], p.target)), "target")
                del pos[k]
        # 3) the close: mark, count, decide exits and entries for the next open
        for k in list(pos):
            p = pos[k]
            s = series[k]
            i = s.index.get(d)
            if i is None:
                # stopped trading: a delisting once it stays gone (no bar for 7 calendar days and none later)
                last = s.days[-1]
                if last < d and (d - last).days > 7:
                    close_trade(p, d, last_close.get(k, p.entry_px) * (1 - DELIST_LOSS), "delisted")
                    del pos[k]
                continue
            last_close[k] = float(s.c[i])
            p.held += 1
            if rule_exit(strategy, s, i) or p.held >= MAX_HOLD[strategy]:
                p.pending_exit = True
        if len(pos) < SLOTS:
            free = [k for _, k in cands.get(d, ()) if k not in pos]
            pending = free[: SLOTS - len(pos) + 5]  # a few spares for names that do not open tomorrow
        value = cash + sum(p.qty * last_close.get(k, p.entry_px) for k, p in pos.items())
        equity.append((d, value))
        if pos:
            exposure_days += 1
    return {"equity": equity, "trades": trades, "exposure": exposure_days / len(cal) if cal else 0.0}


# ---------------------------------------------------------------------- metrics
def stats(equity: list[tuple[date, float]], trades: list[Trade] | None = None) -> dict[str, Any]:
    if len(equity) < 2:
        return {"cagr": None, "sharpe": None, "max_drawdown": None, "trades": len(trades or [])}
    v = np.array([e for _, e in equity])
    years = (equity[-1][0] - equity[0][0]).days / 365.25
    cagr = (v[-1] / v[0]) ** (1 / years) - 1 if years > 0 and v[0] > 0 else None
    r = v[1:] / v[:-1] - 1
    sharpe = float(np.mean(r) / np.std(r) * math.sqrt(252)) if np.std(r) > 0 else None
    peak = np.maximum.accumulate(v)
    out: dict[str, Any] = {"start": equity[0][0].isoformat(), "end": equity[-1][0].isoformat(), "cagr": cagr, "sharpe": sharpe,
                           "max_drawdown": float(np.min(v / peak - 1)), "total_return": float(v[-1] / v[0] - 1)}
    if trades is not None:
        rets = [t.ret for t in trades]
        out |= {"trades": len(trades), "win_rate": float(np.mean([x > 0 for x in rets])) if rets else None,
                "avg_trade": float(np.mean(rets)) if rets else None, "avg_sessions": float(np.mean([t.sessions for t in trades])) if trades else None,
                "exit_reasons": {k: sum(1 for t in trades if t.reason == k) for k in sorted({t.reason for t in trades})}}
    return out


def buy_and_hold(s: Series, start: date, end: date) -> list[tuple[date, float]]:
    return [(d, float(s.c[i])) for d, i in s.index.items() if start <= d <= end]


def verdict(train: dict[str, Any], hold: dict[str, Any], spy_hold: dict[str, Any]) -> dict[str, Any]:
    """§17's adoption rule (all four)."""
    checks = {
        "holdout_cagr_positive": bool(hold.get("cagr") is not None and hold["cagr"] > 0),
        "holdout_sharpe_beats_spy": bool(hold.get("sharpe") is not None and spy_hold.get("sharpe") is not None and hold["sharpe"] > spy_hold["sharpe"]),
        "train_cagr_positive": bool(train.get("cagr") is not None and train["cagr"] > 0),
        "holdout_trades_30": bool((hold.get("trades") or 0) >= 30),
    }
    beats = bool(hold.get("cagr") is not None and spy_hold.get("cagr") is not None and hold["cagr"] > spy_hold["cagr"])
    return {"checks": checks, "adopt": all(checks.values()), "beats_spy_holdout": beats}


def evaluate(series: dict[str, Series], spy: Series, calendar: list[date], strategies: Sequence[str] = STRATEGIES,
             log: Callable[[str], None] = print) -> dict[str, Any]:
    spy_train, spy_hold = stats(buy_and_hold(spy, *TRAIN)), stats(buy_and_hold(spy, *HOLDOUT))
    full = (TRAIN[0], HOLDOUT[1])
    out: dict[str, Any] = {"spy": {"train": spy_train, "holdout": spy_hold, "full": stats(buy_and_hold(spy, *full))}, "strategies": {}}
    for st in strategies:
        res: dict[str, Any] = {"name": NAMES[st]}
        cands = candidates(st, series, spy)
        for cost, tag in ((0.001, ""), (0.002, "_cost2x")):
            for (a, b), part in ((TRAIN, "train"), (HOLDOUT, "holdout"), (full, "full")):
                sim = simulate(st, series, calendar, spy, cost=cost, start=a, end=b, cands=cands)
                res[part + tag] = stats(sim["equity"], sim["trades"]) | {"exposure": sim["exposure"]}
                log(f"{st} {part}{tag}: {json.dumps({k: v for k, v in res[part + tag].items() if k != 'exit_reasons'}, default=str)}")
        res["verdict"] = verdict(res["train"], res["holdout"], spy_hold)
        out["strategies"][st] = res
    return out


# ---------------------------------------------------------------------- the database
def load(db: str) -> tuple[dict[str, Series], Series, list[date]]:
    """Stocks of the app's universe (common stock / ADR on the three US exchanges, unresolved listings out) and SPY."""
    from marketlens.backtest.schema import bt_engine
    from marketlens.backtest.store import BacktestData
    from marketlens.infrastructure.db.session import make_session_factory

    eng = bt_engine(db)
    data = BacktestData(eng, make_session_factory(eng))
    series: dict[str, Series] = {}
    spy: Series | None = None
    for ln in data.lineages:
        if len(ln.days) < 260:
            continue
        is_spy = "SPY" in set(ln.labels) and (ln.type or "") in ("ETF", "ETV", "ETS", "FUND", "")
        if not is_spy and not ((ln.type or "") in STOCK_TYPES and (ln.exchange or "") in EXCHANGES):
            continue
        o, h, lo, c, v, raw = adjusted(ln.days, ln.rows, [(s.execution_date, s.split_to / s.split_from) for s in ln.splits if s.split_from > 0])
        s = Series(ln.key, list(ln.days), o, h, lo, c, v, raw)
        indicators(s)
        if is_spy:
            if spy is None or len(s.days) > len(spy.days):
                spy = s
        else:
            series[ln.key] = s
    if spy is None:
        raise SystemExit("SPY bars not found in the backtest database")
    return series, spy, list(spy.days)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    series, spy, cal = load(a.db)
    print(f"securities {len(series)} · SPY sessions {len(cal)} ({cal[0]} ~ {cal[-1]})", flush=True)
    res = evaluate(series, spy, cal)
    res["source"] = {"securities": len(series), "sessions": len(cal), "prereg": "PREREGISTRATION §17"}
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)
    print("=== verdicts", json.dumps({k: v["verdict"] for k, v in res["strategies"].items()}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
