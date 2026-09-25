"""Recommendation outcomes, IC / IR — with strict point-in-time maturity rules.

An outcome for horizon ``h`` is *mature* only when ``h`` full trading sessions have closed after the
recommendation session. Immature outcomes are excluded from every calibration dataset.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import fmean, stdev
from typing import Mapping, Sequence

from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import add_trading_days, trading_days_between

HORIZONS = (1, 5, 20, 60)


def is_mature(rec_day: date, horizon: int, as_of: date) -> bool:
    return trading_days_between(rec_day, as_of) >= horizon


def forward_return(bars: Sequence[Bar], rec_day: date, horizon: int, as_of: date) -> float | None:
    """Close-to-close return from the recommendation session close to ``horizon`` sessions later.

    Returns None unless the horizon has fully elapsed by ``as_of`` (no look-ahead)."""
    if not is_mature(rec_day, horizon, as_of):
        return None
    by_day = {b.day: b for b in bars if b.day <= as_of}
    base = by_day.get(rec_day)
    target = by_day.get(add_trading_days(rec_day, horizon))
    # exact sessions only: a missing base or target bar (halt, delisting, data gap) → no outcome rather
    # than silently substituting a neighbouring day
    if base is None or target is None or base.close <= 0:
        return None
    return target.close / base.close - 1


@dataclass(frozen=True, slots=True)
class ForwardOutcome:
    value: float | None
    status: str  # OK | DELISTED_LAST_PRICE | MISSING_BARS | IMMATURE
    target_day: date
    note: str


def forward_outcome(bars: Sequence[Bar], rec_day: date, horizon: int, as_of: date, delisted_on: date | None = None) -> ForwardOutcome:
    """Like :func:`forward_return` but explains itself and handles delistings.

    A name delisted before the horizon ends is valued at its last close before delisting (the true
    delisting proceeds are unknown from free data — flagged, never silently dropped: dropping them would
    bias outcomes toward survivors). A missing bar for a listed name (halt, data gap) → MISSING_BARS."""
    target_day = add_trading_days(rec_day, horizon)
    if not is_mature(rec_day, horizon, as_of):
        return ForwardOutcome(None, "IMMATURE", target_day, f"{horizon}거래일 미경과")
    by_day = {b.day: b for b in bars if b.day <= as_of}
    base = by_day.get(rec_day)
    if base is None or base.close <= 0:
        return ForwardOutcome(None, "MISSING_BARS", target_day, f"기준일 {rec_day.isoformat()} 종가 없음")
    if delisted_on is not None and delisted_on <= target_day:
        last = [b for d, b in sorted(by_day.items()) if rec_day <= d < delisted_on]
        if not last:
            return ForwardOutcome(None, "MISSING_BARS", target_day, "상장폐지 전 가격 없음")
        return ForwardOutcome(last[-1].close / base.close - 1, "DELISTED_LAST_PRICE", target_day, f"{delisted_on.isoformat()} 상장폐지 → 마지막 종가({last[-1].day.isoformat()}) 기준, 실제 청산가와 다를 수 있음")
    target = by_day.get(target_day)
    if target is None:
        return ForwardOutcome(None, "MISSING_BARS", target_day, f"목표일 {target_day.isoformat()} 종가 없음(거래정지/데이터 누락)")
    return ForwardOutcome(target.close / base.close - 1, "OK", target_day, "")


def dedupe_samples(samples: Sequence["OutcomeSample"]) -> list["OutcomeSample"]:
    """One sample per (ticker, recommendation day): repeated intraday scans are not independent evidence."""
    seen: set[tuple[str, date]] = set()
    out = []
    for s in samples:
        k = (s.ticker, s.rec_day)
        if k not in seen:
            seen.add(k)
            out.append(s)
    return out


def non_overlapping(samples: Sequence["OutcomeSample"], horizon: int) -> list["OutcomeSample"]:
    """Per ticker, keep samples at least ``horizon`` trading days apart so holding periods don't overlap."""
    out = []
    last: dict[str, date] = {}
    for s in sorted(dedupe_samples(samples), key=lambda x: (x.ticker, x.rec_day)):
        prev = last.get(s.ticker)
        if prev is None or trading_days_between(prev, s.rec_day) >= horizon:
            out.append(s)
            last[s.ticker] = s.rec_day
    return out


def rankdata(values: Sequence[float]) -> list[float]:
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = fmean(xs), fmean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


def spearman(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return _pearson(rankdata(xs), rankdata(ys))


@dataclass(frozen=True, slots=True)
class OutcomeSample:
    """One recommendation's factor scores at decision time and its realised forward returns."""

    ticker: str
    rec_day: date
    factors: Mapping[str, float]
    forward_returns: Mapping[int, float | None]
    sector: str = ""
    regime: str = ""


@dataclass(frozen=True, slots=True)
class FactorIC:
    factor: str
    horizon: int
    ic: float | None
    ir: float | None
    samples: int
    periods: int


def factor_ic(
    samples: Sequence[OutcomeSample],
    factor: str,
    horizon: int,
    as_of: date,
    min_samples: int = 30,
    min_period_samples: int = 5,
    min_periods: int = 5,
) -> FactorIC:
    usable = [
        s for s in dedupe_samples(samples)
        if is_mature(s.rec_day, horizon, as_of)
        and s.forward_returns.get(horizon) is not None
        and s.factors.get(factor) is not None
    ]
    xs = [float(s.factors[factor]) for s in usable]
    ys = [float(s.forward_returns[horizon]) for s in usable]  # type: ignore[arg-type]
    ic = spearman(xs, ys) if len(usable) >= min_samples else None
    by_day: dict[date, list[OutcomeSample]] = {}
    for s in usable:
        by_day.setdefault(s.rec_day, []).append(s)
    period_ics: list[float] = []
    for group in by_day.values():
        if len(group) < min_period_samples:
            continue
        v = spearman([float(g.factors[factor]) for g in group], [float(g.forward_returns[horizon]) for g in group])  # type: ignore[arg-type]
        if v is not None:
            period_ics.append(v)
    ir = None
    if len(period_ics) >= min_periods:
        sd = stdev(period_ics)
        ir = fmean(period_ics) / sd if sd > 0 else None
    return FactorIC(factor, horizon, round(ic, 4) if ic is not None else None, round(ir, 4) if ir is not None else None, len(usable), len(period_ics))


def bucket_performance(samples: Sequence[OutcomeSample], key: str, horizon: int, as_of: date, edges: Sequence[float]) -> list[tuple[str, int, float | None]]:
    """Average forward return per bucket of a factor (e.g. score buckets)."""
    out: list[tuple[str, int, float | None]] = []
    bounds = list(edges)
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        rets = [
            s.forward_returns[horizon]
            for s in samples
            if is_mature(s.rec_day, horizon, as_of)
            and s.forward_returns.get(horizon) is not None
            and s.factors.get(key) is not None
            and lo <= s.factors[key] < hi
        ]
        vals = [r for r in rets if r is not None]
        out.append((f"{lo:g}-{hi:g}", len(vals), fmean(vals) if vals else None))
    return out


def rolling_ic(
    samples: Sequence[OutcomeSample], factor: str, horizon: int, as_of: date, window_sessions: int = 60, step_sessions: int = 20, min_samples: int = 30
) -> list[tuple[date, float | None, int]]:
    """IC in rolling windows of recommendation dates (end date, IC or None, sample count)."""
    usable = [s for s in dedupe_samples(samples) if is_mature(s.rec_day, horizon, as_of) and s.forward_returns.get(horizon) is not None and s.factors.get(factor) is not None]
    if not usable:
        return []
    days = sorted({s.rec_day for s in usable})
    out: list[tuple[date, float | None, int]] = []
    end = days[-1]
    first = days[0]
    while end >= first:
        start = add_trading_days(end, -window_sessions)
        win = [s for s in usable if start < s.rec_day <= end]
        ic = spearman([float(s.factors[factor]) for s in win], [float(s.forward_returns[horizon]) for s in win]) if len(win) >= min_samples else None  # type: ignore[arg-type]
        out.append((end, round(ic, 4) if ic is not None else None, len(win)))
        end = add_trading_days(end, -step_sessions)
    return list(reversed(out))
