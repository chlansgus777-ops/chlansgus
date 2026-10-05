"""Trading strategies with explicit rules (owner 2026-10-06: "명확한 매매 전략을 검증하고, 근거가 확인된 전략의 신호를
제공하는 앱"). ONE calculation used by both the daily backtest (backtest/portfolio.py) and the live app
(application/strategy_signals.py): only the data supply and the fills differ.

The flow is kept apart on purpose:
  tradable universe → a strategy's entry signal → its exit rule → account risk and money (the caller) → is the signal
  still valid now (the caller, with the price's age)

The composite score, its 72/66 thresholds and the analysis' price plan (max buy, R/R 2.0, resistance targets) are NOT
part of any strategy here. The numbers below are first hypotheses to be tested (docs/strategies/STRATEGIES.md,
PREREGISTRATION §19), never "optimised" values.

All inputs are daily bars on one share basis (split-adjusted), oldest first; index i = the session whose close decides.
Every rule reads bars up to and including i only; references that must exclude "today" (the breakout high, the average
volume, the exit low) read i−n … i−1.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import numpy as np

# ---------------------------------------------------------------------- the strategies
MIN_HISTORY = 252  # sessions of bars a name needs before any strategy looks at it (the 200-day line + a year)
MIN_PRICE = 5.0  # the close as traded (not split-adjusted)
MIN_DOLLAR_VOLUME = 20e6  # 20-session average of close × volume


@dataclass(frozen=True)
class StrategySpec:
    id: str
    name: str
    version: str
    entry: tuple[str, ...]
    exit: tuple[str, ...]
    max_hold: int | None  # sessions; None = no time exit
    needs_spy: bool


A = StrategySpec(
    "A", "상승 추세 눌림목", "A-1.0",
    entry=("종가 > 200일 단순이동평균", "오늘 50일 단순이동평균 > 20거래일 전 50일 단순이동평균",
           "최근 2거래일 연속 종가 하락", "RSI(2, Wilder) ≤ 10"),
    exit=("종가 > 5일 단순이동평균이면 다음 거래일 시가에 매도", "또는 보유 10거래일째 종가 뒤 다음 거래일 시가에 매도"),
    max_hold=10, needs_spy=False)
C = StrategySpec(
    "C", "돌파 추세 추종", "C-1.0",
    entry=("오늘 종가 > 직전 20거래일 최고가(오늘 제외)", "오늘 거래량 ≥ 직전 20거래일 평균 거래량 × 1.5(오늘 제외)",
           "SPY 종가 > SPY 200일 단순이동평균"),
    exit=("종가 < 직전 10거래일 최저가(오늘 제외)이면 다음 거래일 시가에 매도", "목표가·고정 수익률 익절 없음 · 기간 제한 없음"),
    max_hold=None, needs_spy=True)
STRATEGIES: dict[str, StrategySpec] = {"A": A, "C": C}

# the registered variants of PREREGISTRATION §19 (not separate strategies: the same signals with one rule added)
STOP_ATR = 2.0  # variant "+손절": stop = entry price − 2 × ATR(14) of the signal day
FUNDAMENTAL_MIN = 0.6  # variant "+재무": the analysis' fundamental sub-score (0–1) at the last weekly analysis ≥ this


# ---------------------------------------------------------------------- indicators (pure numpy)
def sma(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= n:
        c = np.cumsum(np.insert(np.asarray(x, dtype=float), 0, 0.0))
        out[n - 1:] = (c[n:] - c[:-n]) / n
    return out


def wilder(x: np.ndarray, n: int) -> np.ndarray:
    """Wilder's average: seed = mean of the first n, then (prev × (n − 1) + x) / n."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    prev = float(np.mean(x[:n]))
    out[n - 1] = prev
    k = (n - 1) / n
    for i in range(n, len(x)):
        prev = prev * k + float(x[i]) / n
        out[i] = prev
    return out


def rsi(close: np.ndarray, n: int = 2) -> np.ndarray:
    out = np.full(len(close), np.nan)
    if len(close) <= n:
        return out
    d = np.diff(np.asarray(close, dtype=float))
    up, dn = wilder(np.clip(d, 0, None), n), wilder(np.clip(-d, 0, None), n)
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.where(dn == 0, np.where(up == 0, 50.0, 100.0), 100.0 - 100.0 / (1.0 + up / np.where(dn == 0, 1.0, dn)))
    r = np.where(np.isnan(up), np.nan, r)
    out[1:] = r
    return out


def atr(high: np.ndarray, low: np.ndarray, close: np.ndarray, n: int = 14) -> np.ndarray:
    prev = np.insert(close[:-1], 0, close[0])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev), np.abs(low - prev)))
    return wilder(tr, n)


def _prior(x: np.ndarray, n: int, fn: Callable[..., np.ndarray]) -> np.ndarray:
    """fn over the n values BEFORE each index (today excluded); NaN until n earlier values exist."""
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = fn(np.lib.stride_tricks.sliding_window_view(np.asarray(x, dtype=float), n)[:-1], axis=1)
    return out


def prior_max(x: np.ndarray, n: int) -> np.ndarray:
    return _prior(x, n, np.max)


def prior_min(x: np.ndarray, n: int) -> np.ndarray:
    return _prior(x, n, np.min)


def prior_mean(x: np.ndarray, n: int) -> np.ndarray:
    return _prior(x, n, np.mean)


def shift(x: np.ndarray, n: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) > n:
        out[n:] = x[:-n]
    return out


def indicators(o: np.ndarray, h: np.ndarray, lo: np.ndarray, c: np.ndarray, v: np.ndarray, raw_close: np.ndarray) -> dict[str, np.ndarray]:
    sma50 = sma(c, 50)
    return {
        "sma5": sma(c, 5), "sma50": sma50, "sma50_20ago": shift(sma50, 20), "sma200": sma(c, 200),
        "rsi2": rsi(c, 2), "atr14": atr(h, lo, c, 14),
        "high20_prior": prior_max(h, 20), "low10_prior": prior_min(lo, 10), "vol20_prior": prior_mean(v, 20),
        "down1": np.concatenate([[False], c[1:] < c[:-1]]) if len(c) else np.array([], dtype=bool),
        "dv20": sma(c * v, 20),  # close × volume on one basis: the split factor cancels
        "raw_close": np.asarray(raw_close, dtype=float), "close": np.asarray(c, dtype=float), "volume": np.asarray(v, dtype=float),
    }


# ---------------------------------------------------------------------- the rules
def tradable(ind: Mapping[str, np.ndarray]) -> np.ndarray:
    """The common universe (every strategy): a year of history, $5 as traded, $20M a day traded on average."""
    n = len(ind["close"])
    with np.errstate(invalid="ignore"):
        return (np.arange(n) >= MIN_HISTORY - 1) & (ind["raw_close"] >= MIN_PRICE) & (ind["dv20"] >= MIN_DOLLAR_VOLUME)


def entry_A(ind: Mapping[str, np.ndarray]) -> np.ndarray:
    c = ind["close"]
    down2 = ind["down1"] & np.concatenate([[False], ind["down1"][:-1]]) if len(c) else ind["down1"]
    with np.errstate(invalid="ignore"):
        return (c > ind["sma200"]) & (ind["sma50"] > ind["sma50_20ago"]) & down2 & (ind["rsi2"] <= 10)


def entry_C(ind: Mapping[str, np.ndarray], spy_up: np.ndarray) -> np.ndarray:
    c, v = ind["close"], ind["volume"]
    with np.errstate(invalid="ignore"):
        return (c > ind["high20_prior"]) & (ind["vol20_prior"] > 0) & (v >= 1.5 * ind["vol20_prior"]) & spy_up


def priority(strategy: str, ind: Mapping[str, np.ndarray]) -> np.ndarray:
    """The order of the day's signals when money runs short (fixed before results): A the most oversold first,
    C the largest volume surge first; ties by ticker (the caller)."""
    if strategy == "A":
        return -ind["rsi2"]
    with np.errstate(invalid="ignore", divide="ignore"):
        return ind["volume"] / np.where(ind["vol20_prior"] > 0, ind["vol20_prior"], np.nan)


def signals(strategy: str, ind: Mapping[str, np.ndarray], spy_up: np.ndarray | None = None) -> np.ndarray:
    """The entry signals of a strategy at each close, inside the common universe."""
    base = tradable(ind)
    if strategy == "A":
        return base & entry_A(ind)
    if strategy == "C":
        if spy_up is None:
            raise ValueError("C needs SPY above its 200-day line, aligned to the name's sessions")
        return base & entry_C(ind, spy_up)
    raise ValueError(strategy)


def exit_due(strategy: str, ind: Mapping[str, np.ndarray], i: int, held: int) -> str | None:
    """Whether a position must be sold at the next open, decided at the close of index i after ``held`` closes in it
    (the entry session's close is the first). Both reasons on one day: the rule exit is recorded (it is checked first);
    either way the sale is the next open."""
    c = ind["close"][i]
    if strategy == "A":
        if c > ind["sma5"][i]:
            return "rule"
        if held >= (A.max_hold or 0):
            return "time"
        return None
    if strategy == "C":
        lo = ind["low10_prior"][i]
        return "rule" if lo == lo and c < lo else None
    raise ValueError(strategy)


# ---------------------------------------------------------------------- what a strategy needs (live: hold, never guess)
def data_holds(strategy: str, sessions: int, has_volume: bool, spy_sessions: int | None) -> list[str]:
    """Why a strategy cannot judge a name with the data at hand (empty = it can). The common execution blocks — a stale
    or wrong price, a halted name — are the caller's (they apply to every strategy)."""
    out: list[str] = []
    if sessions < MIN_HISTORY:
        out.append(f"일봉 {sessions}거래일 — {MIN_HISTORY}거래일 필요(200일선과 1년 거래 이력)")
    if strategy == "C":
        if not has_volume:
            out.append("오늘 거래량 없음 — 돌파의 거래량 조건을 판단할 수 없음")
        if spy_sessions is None or spy_sessions < 200:
            out.append("SPY 일봉 200거래일 미만 — 시장 추세 조건을 판단할 수 없음")
    return out


def explain_entry(strategy: str, ind: Mapping[str, np.ndarray], i: int, spy_up: bool | None = None) -> list[dict[str, Any]]:
    """Each entry condition with the numbers it used (the screen shows why a signal is or is not there)."""
    c = float(ind["close"][i])
    f = lambda x: None if x is None or x != x else round(float(x), 4)  # noqa: E731
    if strategy == "A":
        d1 = bool(ind["down1"][i])
        d0 = bool(ind["down1"][i - 1]) if i > 0 else False
        return [
            {"rule": A.entry[0], "ok": bool(c > ind["sma200"][i]), "value": f(c), "ref": f(ind["sma200"][i])},
            {"rule": A.entry[1], "ok": bool(ind["sma50"][i] > ind["sma50_20ago"][i]), "value": f(ind["sma50"][i]), "ref": f(ind["sma50_20ago"][i])},
            {"rule": A.entry[2], "ok": d1 and d0, "value": None, "ref": None},
            {"rule": A.entry[3], "ok": bool(ind["rsi2"][i] <= 10), "value": f(ind["rsi2"][i]), "ref": 10.0},
        ]
    vp = ind["vol20_prior"][i]
    return [
        {"rule": C.entry[0], "ok": bool(c > ind["high20_prior"][i]), "value": f(c), "ref": f(ind["high20_prior"][i])},
        {"rule": C.entry[1], "ok": bool(vp > 0 and ind["volume"][i] >= 1.5 * vp), "value": f(ind["volume"][i]), "ref": f(1.5 * vp if vp == vp else None)},
        {"rule": C.entry[2], "ok": bool(spy_up), "value": None, "ref": None},
    ]


def split_adjust(days: Sequence[Any], rows: Sequence[tuple[float, float, float, float, float]], splits: Sequence[tuple[Any, float]]) -> tuple[np.ndarray, ...]:
    """(o, h, l, c, v, raw close, factor) on the newest share basis: prices before a split divided by its ratio, volume
    multiplied. Ratios are all the rules use, so a later split changes no signal; the $5 floor reads the raw close."""
    a = np.array(rows, dtype=float).reshape(-1, 5)
    f = np.ones(len(days))
    dd = np.array([d.toordinal() for d in days])
    for ex, ratio in splits:
        if ratio > 0 and ratio != 1:
            f[dd < ex.toordinal()] *= ratio
    return a[:, 0] / f, a[:, 1] / f, a[:, 2] / f, a[:, 3] / f, a[:, 4] * f, a[:, 3], f
