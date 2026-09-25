"""Portfolio exposure analysis (deterministic). No order execution exists anywhere in MarketLens."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping, Sequence

from marketlens.domain.enums import SizeClass


@dataclass(frozen=True, slots=True)
class Holding:
    ticker: str
    quantity: float
    cost_basis: float
    sector: str
    themes: tuple[str, ...] = ()  # e.g. ("AI",)
    rate_sensitivity: float = 0.0


@dataclass(frozen=True, slots=True)
class Portfolio:
    holdings: tuple[Holding, ...]
    cash: float


@dataclass(frozen=True, slots=True)
class PortfolioLimits:
    max_single_name: float = 0.10
    max_sector: float = 0.30
    max_theme: float = 0.40
    high_correlation: float = 0.75
    full_position: float = 0.05
    half_position: float = 0.025
    small_position: float = 0.0125


@dataclass(frozen=True, slots=True)
class CandidateProfile:
    ticker: str
    sector: str
    themes: tuple[str, ...]
    rate_sensitivity: float
    returns: Sequence[float] = field(default_factory=tuple)


def pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    n = min(len(xs), len(ys))
    if n < 20:
        return None
    x, y = list(xs[-n:]), list(ys[-n:])
    mx, my = sum(x) / n, sum(y) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(x, y))
    vx = sum((a - mx) ** 2 for a in x)
    vy = sum((b - my) ** 2 for b in y)
    if vx <= 0 or vy <= 0:
        return None
    return cov / (vx * vy) ** 0.5


@dataclass(frozen=True, slots=True)
class PortfolioReview:
    total_value: float
    sector_weights: Mapping[str, float]
    theme_weights: Mapping[str, float]
    top_holding_weight: float
    hhi: float
    candidate_sector_weight_after: float
    candidate_theme_weights_after: Mapping[str, float]
    avg_correlation_to_holdings: float | None
    max_correlation: tuple[str, float] | None
    portfolio_rate_sensitivity: float
    size_cap: SizeClass
    warnings: tuple[str, ...]
    fit: str  # GOOD | NEUTRAL | POOR


def review_candidate(
    pf: Portfolio,
    prices: Mapping[str, float],
    cand: CandidateProfile,
    holding_returns: Mapping[str, Sequence[float]],
    limits: PortfolioLimits | None = None,
) -> PortfolioReview:
    limits = limits or PortfolioLimits()
    values = {h.ticker: h.quantity * prices.get(h.ticker, h.cost_basis) for h in pf.holdings}
    total = sum(values.values()) + pf.cash
    total = total if total > 0 else 1.0
    sector_w: dict[str, float] = {}
    theme_w: dict[str, float] = {}
    rate = 0.0
    for h in pf.holdings:
        w = values[h.ticker] / total
        sector_w[h.sector] = sector_w.get(h.sector, 0.0) + w
        for t in h.themes:
            theme_w[t] = theme_w.get(t, 0.0) + w
        rate += w * h.rate_sensitivity
    weights = [v / total for v in values.values()]
    hhi = sum(w * w for w in weights)
    add = limits.full_position
    sector_after = sector_w.get(cand.sector, 0.0) + add
    theme_after = {t: theme_w.get(t, 0.0) + add for t in cand.themes}

    corrs: list[tuple[str, float]] = []
    for t, r in holding_returns.items():
        c = pearson(cand.returns, r)
        if c is not None:
            corrs.append((t, c))
    avg_c = sum(c for _, c in corrs) / len(corrs) if corrs else None
    max_c = max(corrs, key=lambda x: x[1]) if corrs else None

    warnings: list[str] = []
    cap = SizeClass.FULL
    rank = {SizeClass.FULL: 3, SizeClass.HALF: 2, SizeClass.SMALL: 1, SizeClass.WATCH: 0}

    def limit(to: SizeClass, why: str) -> None:
        nonlocal cap
        if rank[to] < rank[cap]:
            cap = to
        warnings.append(why)

    if cand.ticker in values and values[cand.ticker] / total >= limits.max_single_name:
        limit(SizeClass.WATCH, f"{cand.ticker} already {values[cand.ticker] / total:.0%} of portfolio (max {limits.max_single_name:.0%})")
    if sector_w.get(cand.sector, 0.0) >= limits.max_sector:
        limit(SizeClass.WATCH, f"sector {cand.sector} already {sector_w[cand.sector]:.0%} (max {limits.max_sector:.0%})")
    elif sector_after > limits.max_sector:
        limit(SizeClass.SMALL, f"sector {cand.sector} would reach {sector_after:.0%} (max {limits.max_sector:.0%})")
    for t, w in theme_after.items():
        if w > limits.max_theme:
            limit(SizeClass.SMALL, f"theme {t} exposure would reach {w:.0%} (max {limits.max_theme:.0%})")
    if max_c is not None and max_c[1] >= limits.high_correlation:
        limit(SizeClass.HALF, f"high correlation {max_c[1]:.2f} with {max_c[0]} (overlap risk)")
    if abs(rate + add * cand.rate_sensitivity) > 0.5 and (rate * cand.rate_sensitivity) > 0:
        limit(SizeClass.HALF, "adds to an already concentrated rate sensitivity")
    if pf.cash / total < limits.small_position:
        limit(SizeClass.WATCH, "insufficient cash")
    fit = "GOOD" if cap == SizeClass.FULL else "NEUTRAL" if cap == SizeClass.HALF else "POOR"
    return PortfolioReview(
        total_value=round(total, 2),
        sector_weights={k: round(v, 4) for k, v in sector_w.items()},
        theme_weights={k: round(v, 4) for k, v in theme_w.items()},
        top_holding_weight=round(max(weights), 4) if weights else 0.0,
        hhi=round(hhi, 4),
        candidate_sector_weight_after=round(sector_after, 4),
        candidate_theme_weights_after={k: round(v, 4) for k, v in theme_after.items()},
        avg_correlation_to_holdings=round(avg_c, 4) if avg_c is not None else None,
        max_correlation=(max_c[0], round(max_c[1], 4)) if max_c else None,
        portfolio_rate_sensitivity=round(rate, 4),
        size_cap=cap,
        warnings=tuple(warnings),
        fit=fit,
    )
