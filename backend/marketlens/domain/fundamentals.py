"""Fundamental statements and deterministic metrics.

Point-in-time: each quarter carries ``filed_date`` (earliest filing) and optionally ``field_filed``
(per-field first publication date). :func:`as_of` drops quarters that had not been filed yet AND blanks
individual fields that were published later (e.g. a Q4 value only disclosed in the 10-K), so historical
analyses never see data that was not yet public.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class QuarterlyFinancials:
    period_end: date
    filed_date: date
    fiscal_label: str
    source: str
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    eps_diluted: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None  # positive number = cash spent
    sbc: float | None = None
    depreciation_amortization: float | None = None
    cash: float | None = None
    total_debt: float | None = None
    total_equity: float | None = None
    shares_diluted: float | None = None
    inventory: float | None = None
    shares_outstanding: float | None = None  # cover-page shares outstanding (for market cap)
    # per-field first publication date; fields missing here are assumed published at ``filed_date``
    field_filed: Mapping[str, date] = field(default_factory=dict)
    # sector specific extras, e.g. {"rpo": .., "nim": .., "cet1": .., "ffo": ..}
    extras: Mapping[str, float] = field(default_factory=dict)


def as_of(quarters: Sequence[QuarterlyFinancials], d: date) -> list[QuarterlyFinancials]:
    out: list[QuarterlyFinancials] = []
    for q in quarters:
        if q.filed_date > d:
            continue
        late = {k: None for k, fd in q.field_filed.items() if fd > d}
        out.append(replace(q, **late) if late else q)
    return sorted(out, key=lambda q: q.period_end)


# Consecutive fiscal quarters end ~91 days apart; allow for 52/53-week calendars.
MIN_QUARTER_GAP_DAYS = 75
MAX_QUARTER_GAP_DAYS = 105


def consecutive(qs: Sequence[QuarterlyFinancials]) -> bool:
    for a, b in zip(qs[:-1], qs[1:]):
        gap = (b.period_end - a.period_end).days
        if gap < MIN_QUARTER_GAP_DAYS or gap > MAX_QUARTER_GAP_DAYS:
            return False
    return True


def _sum4(qs: Sequence[QuarterlyFinancials], attr: str) -> float | None:
    if len(qs) < 4 or not consecutive(qs[-4:]):
        return None  # a TTM over a gap or duplicate quarter would be wrong
    vals = [getattr(q, attr) for q in qs[-4:]]
    if any(v is None for v in vals):
        return None
    return float(sum(vals))


def _growth(cur: float | None, prev: float | None) -> float | None:
    if cur is None or prev is None or prev == 0:
        return None
    if prev < 0:
        # growth from a negative base is not meaningful as a percentage
        return None
    return cur / prev - 1


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def _trend(values: Sequence[float | None]) -> float | None:
    """Least-squares slope normalised by mean absolute level (per quarter)."""
    pts = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(pts) < 3:
        return None
    n = len(pts)
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    den = sum((p[0] - mx) ** 2 for p in pts)
    if den == 0:
        return None
    slope = sum((p[0] - mx) * (p[1] - my) for p in pts) / den
    scale = sum(abs(p[1]) for p in pts) / n
    return slope / scale if scale else None


@dataclass(frozen=True, slots=True)
class FundamentalMetrics:
    quarters_available: int
    latest_period: date | None
    revenue_ttm: float | None
    revenue_growth_yoy: float | None  # latest quarter vs same quarter last year
    revenue_growth_qoq: float | None
    revenue_growth_ttm: float | None
    gross_margin: float | None
    gross_margin_change_yoy: float | None
    operating_margin: float | None
    operating_margin_change_yoy: float | None
    net_income_ttm: float | None
    eps_ttm: float | None
    eps_growth_yoy: float | None
    eps_growth_ttm: float | None
    fcf_ttm: float | None
    fcf_margin: float | None
    cash: float | None
    total_debt: float | None
    total_equity: float | None
    net_debt: float | None
    capex_ttm: float | None
    ebitda_ttm: float | None
    sbc_ttm: float | None
    sbc_pct_revenue: float | None
    roe: float | None
    roic: float | None
    shares_diluted: float | None
    shares_outstanding: float | None
    share_dilution_yoy: float | None
    revenue_trend_4q: float | None
    revenue_trend_8q: float | None
    margin_trend_4q: float | None
    inventory_growth_yoy: float | None
    extras: Mapping[str, float]


def compute_metrics(quarters: Sequence[QuarterlyFinancials], tax_rate: float = 0.21) -> FundamentalMetrics:
    qs = sorted(quarters, key=lambda q: q.period_end)
    n = len(qs)
    last = qs[-1] if qs else None
    prev_q = qs[-2] if n >= 2 and consecutive(qs[-2:]) else None
    yoy_q = qs[-5] if n >= 5 and 350 <= (qs[-1].period_end - qs[-5].period_end).days <= 380 else None

    rev_ttm = _sum4(qs, "revenue")
    rev_ttm_prev = _sum4(qs[:-4], "revenue") if n >= 8 else None
    ni_ttm = _sum4(qs, "net_income")
    eps_ttm = _sum4(qs, "eps_diluted")
    eps_ttm_prev = _sum4(qs[:-4], "eps_diluted") if n >= 8 else None
    ocf_ttm = _sum4(qs, "operating_cash_flow")
    capex_ttm = _sum4(qs, "capex")
    sbc_ttm = _sum4(qs, "sbc")
    op_ttm = _sum4(qs, "operating_income")
    da_ttm = _sum4(qs, "depreciation_amortization")
    ebitda_ttm = op_ttm + da_ttm if op_ttm is not None and da_ttm is not None else None
    fcf_ttm = ocf_ttm - capex_ttm if ocf_ttm is not None and capex_ttm is not None else None

    def margin(q: QuarterlyFinancials | None, attr: str) -> float | None:
        if q is None:
            return None
        return _div(getattr(q, attr), q.revenue)

    gm = margin(last, "gross_profit")
    om = margin(last, "operating_income")
    gm_yoy = margin(yoy_q, "gross_profit")
    om_yoy = margin(yoy_q, "operating_income")

    cash = last.cash if last else None
    debt = last.total_debt if last else None
    equity = last.total_equity if last else None
    net_debt = debt - cash if debt is not None and cash is not None else None
    roe = _div(ni_ttm, equity) if equity and equity > 0 else None
    invested = None
    if debt is not None and equity is not None and cash is not None:
        invested = debt + equity - cash
    roic = _div(op_ttm * (1 - tax_rate), invested) if op_ttm is not None and invested and invested > 0 else None

    rev_series = [q.revenue for q in qs]
    om_series = [margin(q, "operating_income") for q in qs]

    return FundamentalMetrics(
        quarters_available=n,
        latest_period=last.period_end if last else None,
        revenue_ttm=rev_ttm,
        revenue_growth_yoy=_growth(last.revenue if last else None, yoy_q.revenue if yoy_q else None),
        revenue_growth_qoq=_growth(last.revenue if last else None, prev_q.revenue if prev_q else None),
        revenue_growth_ttm=_growth(rev_ttm, rev_ttm_prev),
        gross_margin=gm,
        gross_margin_change_yoy=gm - gm_yoy if gm is not None and gm_yoy is not None else None,
        operating_margin=om,
        operating_margin_change_yoy=om - om_yoy if om is not None and om_yoy is not None else None,
        net_income_ttm=ni_ttm,
        eps_ttm=eps_ttm,
        eps_growth_yoy=_growth(last.eps_diluted if last else None, yoy_q.eps_diluted if yoy_q else None),
        eps_growth_ttm=_growth(eps_ttm, eps_ttm_prev),
        fcf_ttm=fcf_ttm,
        fcf_margin=_div(fcf_ttm, rev_ttm),
        cash=cash,
        total_debt=debt,
        total_equity=equity,
        net_debt=net_debt,
        capex_ttm=capex_ttm,
        ebitda_ttm=ebitda_ttm,
        sbc_ttm=sbc_ttm,
        sbc_pct_revenue=_div(sbc_ttm, rev_ttm),
        roe=roe,
        roic=roic,
        shares_diluted=last.shares_diluted if last else None,
        shares_outstanding=last.shares_outstanding if last else None,
        share_dilution_yoy=_growth(last.shares_diluted if last else None, yoy_q.shares_diluted if yoy_q else None),
        revenue_trend_4q=_trend(rev_series[-4:]),
        revenue_trend_8q=_trend(rev_series[-8:]),
        margin_trend_4q=_trend(om_series[-4:]),
        inventory_growth_yoy=_growth(last.inventory if last else None, yoy_q.inventory if yoy_q else None),
        extras=dict(last.extras) if last else {},
    )


def metrics_as_dict(m: FundamentalMetrics) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    for name in m.__dataclass_fields__:
        v = getattr(m, name)
        if isinstance(v, (int, float)) or v is None:
            out[name] = v
    for k, v in m.extras.items():
        out[k] = v
    rg = m.revenue_growth_yoy
    fm = m.fcf_margin
    out["rule_of_40"] = (rg + fm) * 100 if rg is not None and fm is not None else None
    return out
