"""Annual financials of foreign private issuers (SEC 20-F, IFRS XBRL) — ANNUAL_ONLY.

A 20-F filer publishes audited IFRS statements once a year; its quarterly numbers (6-K) are not XBRL
tagged, so free structured quarterly data does not exist. Nothing is split into "quarters". Values are
in the REPORTING currency (TWD, EUR, …): only currency-free ratios (growth, margins, leverage) are used.
Per-share valuation additionally needs the ADR ratio and an FX rate; without a verified ratio it is not
computed (reported as a limitation, never estimated).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Mapping, Sequence


@dataclass(frozen=True, slots=True)
class AnnualFinancials:
    period_end: date
    filed_date: date
    currency: str
    source: str
    revenue: float | None = None
    gross_profit: float | None = None
    operating_income: float | None = None
    net_income: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    cash: float | None = None
    total_debt: float | None = None
    total_equity: float | None = None
    field_filed: Mapping[str, date] = field(default_factory=dict)


def _r(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b not in (None, 0) else None


def annual_features(years: Sequence[AnnualFinancials], as_of: date) -> dict[str, float | None]:
    """Currency-free features from the latest two fiscal years filed on/before ``as_of``."""
    vis = sorted((y for y in years if y.filed_date <= as_of), key=lambda y: y.period_end)
    if not vis:
        return {}
    cur = vis[-1]
    prev = vis[-2] if len(vis) > 1 and 330 <= (cur.period_end - vis[-2].period_end).days <= 400 else None
    fcf = cur.operating_cash_flow - cur.capex if cur.operating_cash_flow is not None and cur.capex is not None else None
    out: dict[str, float | None] = {
        "revenue_growth_yoy": (cur.revenue / prev.revenue - 1) if prev and cur.revenue and prev.revenue else None,
        "gross_margin": _r(cur.gross_profit, cur.revenue),
        "operating_margin": _r(cur.operating_income, cur.revenue),
        "net_margin": _r(cur.net_income, cur.revenue),
        "fcf_margin": _r(fcf, cur.revenue),
        "capex_to_revenue": _r(cur.capex, cur.revenue),
        "roe": _r(cur.net_income, cur.total_equity),
    }
    if prev and cur.gross_profit is not None and prev.gross_profit is not None and cur.revenue and prev.revenue:
        out["gross_margin_change_yoy"] = cur.gross_profit / cur.revenue - prev.gross_profit / prev.revenue
    return out
