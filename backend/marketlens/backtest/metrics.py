"""Statistics of the backtest (PREREGISTRATION §지표·통계). Pure functions on plain lists — no data access.

- Spearman rank IC per week, ties at average ranks; weeks with fewer than ``min_names`` names are excluded.
- Newey-West t of the mean weekly IC with lag L = ceil(h/5) − 1 (overlapping h-day returns sampled weekly);
  two-sided p from the normal distribution.
- Holm step-down adjustment (family-wise 5 %).
- Sector-neutral IC: factor and return minus their sector means (per week) before the rank IC.
- Risk-adjusted IC: the return's residual after a cross-sectional OLS on beta (252 days) and log market cap.
- 12-week moving-block bootstrap of a weekly series (seeded) for a 90 % interval of its mean.
"""

from __future__ import annotations

import math
import random
from statistics import NormalDist
from typing import Sequence

import numpy as np


def ranks(xs: Sequence[float]) -> list[float]:
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = avg
        i = j + 1
    return out


def spearman(x: Sequence[float], y: Sequence[float]) -> float | None:
    if len(x) < 3:
        return None
    rx, ry = np.array(ranks(x)), np.array(ranks(y))
    sx, sy = rx.std(), ry.std()
    if sx == 0 or sy == 0:
        return None  # a constant factor (or return) has no rank information
    return float(((rx - rx.mean()) * (ry - ry.mean())).mean() / (sx * sy))


def nw_lag(h: int) -> int:
    return math.ceil(h / 5) - 1


def newey_west_t(series: Sequence[float], lag: int) -> tuple[float | None, float | None]:
    """(t, two-sided p) of the mean of ``series`` with a Bartlett-kernel Newey-West variance."""
    x = np.asarray(series, dtype=float)
    n = len(x)
    if n < 3:
        return None, None
    e = x - x.mean()
    v = float((e * e).sum()) / n
    for k in range(1, min(lag, n - 1) + 1):
        w = 1 - k / (lag + 1)
        v += 2 * w * float((e[k:] * e[:-k]).sum()) / n
    if v <= 0:
        return None, None
    t = float(x.mean()) / math.sqrt(v / n)
    return t, 2 * (1 - NormalDist().cdf(abs(t)))


def holm(pvalues: dict[str, float | None]) -> dict[str, float | None]:
    """Holm-adjusted p-values (monotone); a missing p stays missing and does not count in the family."""
    items = sorted(((p, k) for k, p in pvalues.items() if p is not None))
    m = len(items)
    out: dict[str, float | None] = {k: None for k, p in pvalues.items() if p is None}
    running = 0.0
    for i, (p, k) in enumerate(items):
        running = max(running, min(1.0, (m - i) * p))
        out[k] = running
    return out


def demean_by(values: Sequence[float], groups: Sequence[str]) -> list[float]:
    sums: dict[str, list[float]] = {}
    for v, g in zip(values, groups):
        sums.setdefault(g, []).append(v)
    means = {g: sum(v) / len(v) for g, v in sums.items()}
    return [v - means[g] for v, g in zip(values, groups)]


def residualize(y: Sequence[float], xs: Sequence[Sequence[float]]) -> list[float] | None:
    """Residuals of an OLS of y on [1, *xs] (cross-section)."""
    if len(y) < len(xs) + 3:
        return None
    X = np.column_stack([np.ones(len(y))] + [np.asarray(c, dtype=float) for c in xs])
    beta, *_ = np.linalg.lstsq(X, np.asarray(y, dtype=float), rcond=None)
    return list(np.asarray(y, dtype=float) - X @ beta)


def block_bootstrap_ci(series: Sequence[float], block: int = 12, n_boot: int = 5000, level: float = 0.90, seed: int = 20260928) -> tuple[float | None, float | None]:
    x = list(series)
    n = len(x)
    if n < block + 1:
        return None, None
    rng = random.Random(seed)
    means = []
    starts = n - block + 1
    for _ in range(n_boot):
        s: list[float] = []
        while len(s) < n:
            i = rng.randrange(starts)
            s.extend(x[i:i + block])
        means.append(sum(s[:n]) / n)
    means.sort()
    lo = means[int((1 - level) / 2 * n_boot)]
    hi = means[int((1 + level) / 2 * n_boot) - 1]
    return lo, hi


def winsorize(xs: Sequence[float], lo: float = 0.01, hi: float = 0.99) -> list[float]:
    if not xs:
        return []
    a = np.asarray(xs, dtype=float)
    ql, qh = np.quantile(a, lo), np.quantile(a, hi)
    return list(np.clip(a, ql, qh))


def quintiles(scores: Sequence[float], n: int = 5) -> list[int]:
    """Quintile (1 = lowest … n = highest) by rank; ties share the average rank."""
    r = ranks(scores)
    m = len(r)
    return [min(n, int((x - 0.5) / m * n) + 1) for x in r]


def ic_summary(weekly: Sequence[float], h: int, block_weeks: int = 26) -> dict[str, object]:
    """Mean IC, Newey-West t / p, share of positive IC in consecutive 26-week blocks, non-overlapping check."""
    t, p = newey_west_t(weekly, nw_lag(h))
    blocks = [weekly[i:i + block_weeks] for i in range(0, len(weekly), block_weeks)]
    blocks = [b for b in blocks if len(b) >= block_weeks // 2]  # a trailing stub under half a block is not a block
    block_means = [sum(b) / len(b) for b in blocks]
    step = max(1, math.ceil(h / 5))
    sparse = list(weekly[::step])
    ts, ps = newey_west_t(sparse, 0)
    return {
        "weeks": len(weekly), "mean_ic": (sum(weekly) / len(weekly)) if weekly else None, "nw_t": t, "p": p, "nw_lag": nw_lag(h),
        "block_means": block_means, "blocks_positive": sum(1 for b in block_means if b > 0), "blocks": len(block_means),
        "nonoverlap": {"step_weeks": step, "weeks": len(sparse), "mean_ic": (sum(sparse) / len(sparse)) if sparse else None, "t": ts, "p": ps},
    }
