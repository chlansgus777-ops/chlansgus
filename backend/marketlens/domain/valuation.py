"""Valuation engine: multiples plus relative comparisons (history, peers, growth, rates)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from marketlens.domain.earnings import AnalystSnapshot
from marketlens.domain.fundamentals import FundamentalMetrics


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _pos_multiple(price_like: float | None, denom: float | None) -> float | None:
    """Multiples on negative/zero denominators are not meaningful → None."""
    if price_like is None or denom is None or denom <= 0:
        return None
    return price_like / denom


@dataclass(frozen=True, slots=True)
class ValuationMultiples:
    price: float | None
    market_cap: float | None
    enterprise_value: float | None
    trailing_pe: float | None
    forward_pe: float | None
    peg: float | None
    price_sales: float | None
    ev_sales: float | None
    ev_ebitda: float | None
    price_fcf: float | None
    fcf_yield: float | None
    earnings_yield: float | None
    forward_earnings_yield: float | None
    p_b: float | None
    p_tbv: float | None
    p_ffo: float | None
    dividend_yield: float | None

    def as_dict(self) -> dict[str, float | None]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}


def compute_multiples(
    price: float | None,
    m: FundamentalMetrics,
    analyst: AnalystSnapshot | None,
    extras: Mapping[str, float] | None = None,
) -> ValuationMultiples:
    extras = extras or {}
    shares = m.shares_diluted
    mcap = price * shares if price is not None and shares else None
    ev = None
    if mcap is not None and m.total_debt is not None and m.cash is not None:
        ev = mcap + m.total_debt - m.cash
    fwd_eps = analyst.forward_eps if analyst else None
    fwd_growth = analyst.forward_eps_growth if analyst else None
    fwd_pe = _pos_multiple(price, fwd_eps)
    growth_pct = None
    if fwd_growth is not None:
        growth_pct = fwd_growth * 100
    elif m.eps_growth_ttm is not None:
        growth_pct = m.eps_growth_ttm * 100
    peg = fwd_pe / growth_pct if fwd_pe is not None and growth_pct is not None and growth_pct > 0 else None
    equity = extras.get("book_value") if extras.get("book_value") else None
    if equity is None and "total_equity" in extras:
        equity = extras["total_equity"]
    tbv = extras.get("tangible_book_value")
    ffo_ttm = extras.get("ffo_ttm")
    dps = extras.get("dividends_per_share_ttm")
    return ValuationMultiples(
        price=price,
        market_cap=mcap,
        enterprise_value=ev,
        trailing_pe=_pos_multiple(price, m.eps_ttm),
        forward_pe=fwd_pe,
        peg=peg,
        price_sales=_pos_multiple(mcap, m.revenue_ttm),
        ev_sales=_pos_multiple(ev, m.revenue_ttm),
        ev_ebitda=_pos_multiple(ev, m.ebitda_ttm),
        price_fcf=_pos_multiple(mcap, m.fcf_ttm),
        fcf_yield=_div(m.fcf_ttm, mcap),
        earnings_yield=_div(m.eps_ttm, price),
        forward_earnings_yield=_div(fwd_eps, price),
        p_b=_pos_multiple(mcap, equity),
        p_tbv=_pos_multiple(mcap, tbv),
        p_ffo=_pos_multiple(mcap, ffo_ttm),
        dividend_yield=_div(dps, price),
    )


def percentile_rank(value: float, history: Sequence[float]) -> float | None:
    """Share of historical observations strictly below ``value`` (0..1)."""
    hs = [h for h in history if h is not None]
    if len(hs) < 8:
        return None
    below = sum(1 for h in hs if h < value)
    equal = sum(1 for h in hs if h == value)
    return (below + 0.5 * equal) / len(hs)


def median(xs: Sequence[float]) -> float | None:
    s = sorted(x for x in xs if x is not None)
    if not s:
        return None
    mid = len(s) // 2
    return s[mid] if len(s) % 2 else (s[mid - 1] + s[mid]) / 2


@dataclass(frozen=True, slots=True)
class RelativeValuation:
    primary_multiple: str
    primary_value: float | None
    history_percentile: float | None  # 0 = cheapest vs own history, 1 = most expensive
    peer_median: float | None
    premium_to_peers: float | None  # 0.2 = 20% premium
    growth_adjusted: float | None  # PEG
    equity_risk_spread: float | None  # forward earnings yield − 10Y yield
    notes: tuple[str, ...]


def relative_valuation(
    multiples: ValuationMultiples,
    primary_multiple: str,
    own_history: Sequence[float],
    peer_values: Sequence[float],
    us10y: float | None,
) -> RelativeValuation:
    pv = multiples.as_dict().get(primary_multiple)
    hist_pct = percentile_rank(pv, own_history) if pv is not None else None
    peer_med = median(peer_values) if len(peer_values) >= 3 else None
    premium = pv / peer_med - 1 if pv is not None and peer_med else None
    spread = None
    if multiples.forward_earnings_yield is not None and us10y is not None:
        spread = multiples.forward_earnings_yield - us10y
    notes: list[str] = []
    if hist_pct is not None and hist_pct > 0.85:
        notes.append(f"{primary_multiple} near the top of its own history")
    if spread is not None and spread < 0:
        notes.append("forward earnings yield below the 10Y Treasury yield (rate-adjusted expensive)")
    if pv is None:
        notes.append(f"primary multiple {primary_multiple} unavailable (negative or missing denominator)")
    return RelativeValuation(
        primary_multiple=primary_multiple,
        primary_value=pv,
        history_percentile=hist_pct,
        peer_median=peer_med,
        premium_to_peers=premium,
        growth_adjusted=multiples.peg,
        equity_risk_spread=spread,
        notes=tuple(notes),
    )


# A cash-generative company has effectively unlimited runway; cap it for scoring purposes.
SELF_FUNDED_RUNWAY_QUARTERS = 40.0


def fundamental_features(m: FundamentalMetrics, extra: Mapping[str, float | None] | None = None) -> dict[str, float | None]:
    """Derived fundamental features used by sector models (beyond the raw metrics)."""
    from marketlens.domain.fundamentals import metrics_as_dict

    d = metrics_as_dict(m)
    d["net_debt_to_ebitda"] = (
        m.net_debt / m.ebitda_ttm if m.net_debt is not None and m.ebitda_ttm and m.ebitda_ttm > 0 else None
    )
    d["capex_to_revenue"] = m.capex_ttm / m.revenue_ttm if m.capex_ttm is not None and m.revenue_ttm else None
    if m.inventory_growth_yoy is not None and m.revenue_growth_yoy is not None:
        d["inventory_vs_revenue_growth"] = m.inventory_growth_yoy - m.revenue_growth_yoy
    else:
        d["inventory_vs_revenue_growth"] = None
    burn = None
    if m.fcf_ttm is not None and m.fcf_ttm < 0:
        burn = -m.fcf_ttm / 4
    if m.cash is not None:
        d["cash_runway_quarters"] = (m.cash / burn) if burn else (SELF_FUNDED_RUNWAY_QUARTERS if m.fcf_ttm is not None else None)
    else:
        d["cash_runway_quarters"] = None
    if extra:
        d.update(extra)
    return d
