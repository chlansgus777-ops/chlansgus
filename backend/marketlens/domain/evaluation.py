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
from marketlens.domain.market_calendar import trading_days_between

HORIZONS = (1, 5, 20, 60)


def is_mature(rec_day: date, horizon: int, as_of: date) -> bool:
    return trading_days_between(rec_day, as_of) >= horizon


def forward_return(bars: Sequence[Bar], rec_day: date, horizon: int, as_of: date) -> float | None:
    """Close-to-close return from the recommendation session close to ``horizon`` sessions later.

    Returns None unless the horizon has fully elapsed by ``as_of`` (no look-ahead)."""
    if not is_mature(rec_day, horizon, as_of):
        return None
    visible = sorted((b for b in bars if b.day <= as_of), key=lambda b: b.day)
    base = next((b for b in reversed(visible) if b.day <= rec_day), None)
    after = [b for b in visible if b.day > rec_day]
    if base is None or len(after) < horizon or base.close <= 0:
        return None
    return after[horizon - 1].close / base.close - 1


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
        s for s in samples
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
