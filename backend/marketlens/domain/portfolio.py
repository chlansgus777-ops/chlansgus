"""Portfolio exposure analysis (deterministic). No order execution exists anywhere in MarketLens."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
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
    returns: Mapping[date, float] = field(default_factory=dict)  # daily returns keyed by trading date


def aligned_pearson(a: Mapping[date, float], b: Mapping[date, float], min_n: int = 20) -> float | None:
    """Correlation on the trading dates both series share (never by array position)."""
    common = sorted(set(a) & set(b))
    if len(common) < min_n:
        return None
    return pearson([a[d] for d in common], [b[d] for d in common])


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
    holding_returns: Mapping[str, Mapping[date, float]],
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
        if t == cand.ticker:
            continue  # an existing position is not "correlated" with itself (ADD is judged by weight caps)
        c = aligned_pearson(cand.returns, r)
        if c is not None:
            corrs.append((t, c))
    avg_c = sum(c for _, c in corrs) / len(corrs) if corrs else None
    max_c = max(corrs, key=lambda x: x[1]) if corrs else None

    warnings: list[str] = [f"{h.ticker}: 현재가 없음 → 매입 단가로 평가(비중 추정 오차 가능)" for h in pf.holdings if h.ticker not in prices]
    cap = SizeClass.FULL
    rank = {SizeClass.FULL: 3, SizeClass.HALF: 2, SizeClass.SMALL: 1, SizeClass.WATCH: 0}

    def limit(to: SizeClass, why: str) -> None:
        nonlocal cap
        if rank[to] < rank[cap]:
            cap = to
        warnings.append(why)

    cur_w = values.get(cand.ticker, 0.0) / total
    if cur_w >= limits.max_single_name:
        limit(SizeClass.WATCH, f"{cand.ticker} 이미 포트폴리오의 {cur_w:.0%} (한도 {limits.max_single_name:.0%})")
    elif cur_w + limits.full_position > limits.max_single_name:
        room = limits.max_single_name - cur_w
        limit(SizeClass.HALF if room >= limits.half_position else SizeClass.SMALL if room >= limits.small_position else SizeClass.WATCH,
              f"추가 시 {cand.ticker} 비중 {cur_w + limits.full_position:.0%} > 한도 {limits.max_single_name:.0%}")
    if sector_w.get(cand.sector, 0.0) >= limits.max_sector:
        limit(SizeClass.WATCH, f"섹터 {cand.sector} 비중 이미 {sector_w[cand.sector]:.0%} (한도 {limits.max_sector:.0%})")
    elif sector_after > limits.max_sector:
        limit(SizeClass.SMALL, f"편입 시 섹터 {cand.sector} 비중 {sector_after:.0%} (한도 {limits.max_sector:.0%})")
    for t, w in theme_after.items():
        if w > limits.max_theme:
            limit(SizeClass.SMALL, f"편입 시 테마 {t} 노출 {w:.0%} (한도 {limits.max_theme:.0%})")
    if max_c is not None and max_c[1] >= limits.high_correlation:
        limit(SizeClass.HALF, f"{max_c[0]}와 상관계수 {max_c[1]:.2f} (중복 위험)")
    if abs(rate + add * cand.rate_sensitivity) > 0.5 and (rate * cand.rate_sensitivity) > 0:
        limit(SizeClass.HALF, "이미 집중된 금리 민감도를 더 키움")
    cash_w = pf.cash / total
    if cash_w < limits.small_position:
        limit(SizeClass.WATCH, "현금 부족")
    elif cash_w < limits.half_position:
        limit(SizeClass.SMALL, f"현금 {cash_w:.1%}: 소량 매수만 가능")
    elif cash_w < limits.full_position:
        limit(SizeClass.HALF, f"현금 {cash_w:.1%}: 절반 규모만 가능")
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


# ------------------------------------------------------------------------------------ snapshot


@dataclass(frozen=True, slots=True)
class HoldingValuation:
    ticker: str
    quantity: float
    cost_basis: float
    price: float | None  # close of the common valuation session (None = no price that day)
    price_day: date | None
    market_value: float | None
    unrealized_pnl: float | None
    unrealized_pct: float | None
    weight: float | None
    sector: str


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    valuation_day: date | None  # every holding is valued at this same session's close
    cash: float
    invested_value: float
    nav: float
    unrealized_pnl: float
    holdings: tuple[HoldingValuation, ...]
    sector_weights: Mapping[str, float]
    theme_weights: Mapping[str, float]
    hhi: float
    beta: float | None  # of the current weights vs the benchmark, on date-aligned returns
    correlations: tuple[tuple[str, str, float], ...]  # pairwise, date-aligned
    missing_prices: tuple[str, ...]
    notes: tuple[str, ...]


def _daily_returns(closes: Mapping[date, float]) -> dict[date, float]:
    days = sorted(closes)
    return {days[i]: closes[days[i]] / closes[days[i - 1]] - 1 for i in range(1, len(days)) if closes[days[i - 1]] > 0}


def portfolio_snapshot(pf: Portfolio, closes: Mapping[str, Mapping[date, float]], benchmark: Mapping[date, float] | None = None, lookback: int = 120) -> PortfolioSnapshot:
    """Consistent valuation: all holdings at the close of the latest session for which EVERY holding has a
    price (so weights never mix today's and last week's prices). Returns/beta/correlations use only the
    trading dates common to the series involved (inner join), never positional alignment."""
    notes: list[str] = []
    tickers = [h.ticker for h in pf.holdings]
    have = [set(closes[t]) for t in tickers if closes.get(t)]
    common = set.intersection(*have) if have else set()
    val_day = max(common) if common else None
    missing = tuple(t for t in tickers if not closes.get(t))
    if missing:
        notes.append(f"가격 데이터 없는 보유 종목(평가금액에서 제외): {', '.join(missing)}")
    if have and val_day is None:
        notes.append("보유 종목들의 공통 가격 거래일이 없음 → 평가금액 계산 불가")
    rows: list[HoldingValuation] = []
    invested = 0.0
    unreal = 0.0
    for h in pf.holdings:
        px = closes.get(h.ticker, {}).get(val_day) if val_day else None
        mv = px * h.quantity if px is not None else None
        pnl = (px - h.cost_basis) * h.quantity if px is not None else None
        if mv is not None:
            invested += mv
            unreal += pnl or 0.0
        rows.append(HoldingValuation(h.ticker, h.quantity, h.cost_basis, px, val_day if px is not None else None, mv, pnl,
                                     (px / h.cost_basis - 1) if px is not None and h.cost_basis > 0 else None, None, h.sector))
    nav = pf.cash + invested
    rows = [replace_weight(r, nav) for r in rows]
    sector_w: dict[str, float] = {}
    theme_w: dict[str, float] = {}
    for h, r in zip(pf.holdings, rows):
        if r.weight is None:
            continue
        sector_w[h.sector] = sector_w.get(h.sector, 0.0) + r.weight
        for t in h.themes:
            theme_w[t] = theme_w.get(t, 0.0) + r.weight
    hhi = sum((r.weight or 0.0) ** 2 for r in rows)
    rets = {t: _daily_returns(closes.get(t, {})) for t in tickers}
    beta = None
    if benchmark and rows and all(r.weight is not None for r in rows) and invested > 0:
        bench_r = _daily_returns(benchmark)
        days = sorted(set(bench_r).intersection(*[set(rets[t]) for t in tickers]))[-lookback:]
        if len(days) >= 40:
            w = {r.ticker: (r.market_value or 0.0) / invested for r in rows}
            port = [sum(w[t] * rets[t][d] for t in tickers) for d in days]
            b = [bench_r[d] for d in days]
            mb, mp = sum(b) / len(b), sum(port) / len(port)
            var = sum((x - mb) ** 2 for x in b)
            if var > 0:
                beta = round(sum((p - mp) * (x - mb) for p, x in zip(port, b)) / var * (invested / nav if nav > 0 else 1.0), 4)
        else:
            notes.append(f"베타 계산 불가: 공통 거래일 {len(days)}일 < 40일")
    corrs: list[tuple[str, str, float]] = []
    for i, a in enumerate(tickers):
        for b_t in tickers[i + 1:]:
            c = aligned_pearson({d: v for d, v in rets[a].items()}, {d: v for d, v in rets[b_t].items()})
            if c is not None:
                corrs.append((a, b_t, round(c, 4)))
    return PortfolioSnapshot(val_day, round(pf.cash, 2), round(invested, 2), round(nav, 2), round(unreal, 2), tuple(rows),
                             {k: round(v, 4) for k, v in sector_w.items()}, {k: round(v, 4) for k, v in theme_w.items()},
                             round(hhi, 4), beta, tuple(corrs), missing, tuple(notes))


def replace_weight(r: HoldingValuation, nav: float) -> HoldingValuation:
    from dataclasses import replace as _replace

    return _replace(r, weight=round(r.market_value / nav, 4) if r.market_value is not None and nav > 0 else None)
