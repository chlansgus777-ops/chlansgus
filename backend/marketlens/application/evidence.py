"""Evidence registry: every fact that an agent or a UI explanation may cite has a stable ID.

Each item carries the semantic metadata the AI output guard needs to verify a numeric claim: which
entity (ticker, or None for market-wide data), which metric, the unit, the period and the timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# units: USD (amounts and per-share prices), KRW, fraction (0.12 = 12%), pct (already in percent: 4.28 = 4.28%),
# x (multiples/ratios), index (unitless level), points (score points), count, shares, thousands, quarters
_EXACT_UNITS: dict[str, str] = {
    "price.current": "USD", "entry.rr": "x", "entry.max_buy": "USD", "entry.stop": "USD", "entry.target1": "USD",
    "tech.rsi14": "index", "tech.rs_6m": "fraction", "tech.volatility_20d": "fraction", "tech.volume_ratio": "x",
    "tech.avg_dollar_volume_20d": "USD",
    "analyst.analyst_count": "count", "analyst.estimate_dispersion": "fraction", "analyst.forward_eps": "USD",
    "analyst.forward_revenue": "USD", "analyst.target_price_consensus": "USD",
    "earnings.last": "fraction", "earnings.revenue_surprise": "fraction", "earnings.guide_rev_vs_cons": "fraction",
    "val.price": "USD", "val.market_cap": "USD", "val.enterprise_value": "USD", "val.fcf_yield": "fraction",
    "val.earnings_yield": "fraction", "val.forward_earnings_yield": "fraction", "val.dividend_yield": "fraction",
    "val.history": "fraction", "val.peers": "fraction", "val.rate_spread": "fraction", "val.peg": "x",
    "issues.net_swing": "points", "options.expected_move": "fraction", "options.iv_rank": "fraction",
    "ownership.short_interest": "fraction", "ownership.insider_net_90d": "USD", "dq.completeness": "fraction",
    "portfolio.sector_after": "fraction", "portfolio.hhi": "index", "portfolio.max_correlation": "index",
}
_MACRO_UNITS: dict[str, str] = {
    "FED_FUNDS": "pct", "US2Y": "pct", "US10Y": "pct", "US30Y": "pct", "CPI_YOY": "pct", "CORE_CPI_YOY": "pct",
    "PCE_YOY": "pct", "CORE_PCE_YOY": "pct", "UNEMPLOYMENT": "pct", "GDP_QOQ_SAAR": "pct", "HY_SPREAD": "pct",
    "PAYROLLS_CHG": "thousands", "VIX": "index", "WTI": "USD", "BRENT": "USD", "GOLD": "USD", "SPX": "index",
    "NASDAQ_COMP": "index", "NDX": "index", "RUT": "index", "SOX": "index", "USD_INDEX": "index",
    "BREADTH_ABOVE_200D": "fraction", "USDKRW": "KRW",
}
_FUND_USD = ("_ttm", "cash", "total_debt", "net_debt", "total_equity", "inventory", "book_value", "tangible_book_value", "ffo")
_FUND_FRACTION = ("margin", "growth", "_yoy", "roic", "roe", "rotce", "_to_revenue", "yield", "_rate", "revenue_share", "payout", "occupancy", "cet1", "nim", "dilution", "conversion", "_vs_revenue_growth", "discount", "pct_revenue", "_to_loans", "trend_")
_FUND_X = ("_to_ebitda", "coverage", "ratio", "book_to_bill", "nrr")


_FORECAST_KEYS = ("forward", "estimate", "consensus", "guide", "target_price", "expected_move", "revision")


def infer_basis(key: str) -> str | None:
    """ACTUAL (reported / measured) vs FORECAST (analyst estimate, guidance, consensus). None = not applicable."""
    head, _, last = key.partition(".")
    if head == "analyst" or any(k in last for k in _FORECAST_KEYS):
        return "FORECAST"
    if head in ("fund", "price", "tech") or key in ("earnings.last", "earnings.revenue_surprise") or last in ("trailing_pe", "earnings_yield"):
        return "ACTUAL"
    return None


def infer_unit(key: str) -> str | None:
    if key in _EXACT_UNITS:
        return _EXACT_UNITS[key]
    head, _, last = key.partition(".")
    if head == "tech":
        return "USD"  # sma/atr/high/low/vwap levels
    if head == "analyst":
        return "fraction" if "revision" in last else None
    if head == "val":
        return "x"
    if head == "score":
        return "points"
    if head == "macro":
        return _MACRO_UNITS.get(last, "points")  # factor contributions are score points
    if head == "fund":
        if last == "eps_ttm" or "per_share" in last or (last.startswith("eps_") and "growth" not in last):
            return "USD"  # per-share amounts
        if last in ("quarters_available", "late_stage_programs"):
            return "count"
        if last.startswith("shares"):
            return "shares"
        if last == "cash_runway_quarters":
            return "quarters"
        if any(s in last for s in _FUND_X):
            return "x"
        if any(s in last for s in _FUND_FRACTION):
            return "fraction"
        if any(last.endswith(s) or last == s for s in _FUND_USD):
            return "USD"
    return None


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    category: str  # price | fundamental | valuation | earnings | analyst | macro | technical | issue | catalyst | options | risk | entry | portfolio | ownership | score
    label: str
    value: float | str | None
    source: str
    source_ts: datetime | None
    quality: str
    metric: str = ""  # canonical key, e.g. "price.current", "fund.eps_ttm"
    ticker: str | None = None  # None = market-wide (macro, regime)
    unit: str | None = None
    period: str | None = None  # e.g. "TTM", "Q2 2026"
    basis: str | None = None  # ACTUAL | FORECAST (None = not a reported/forecast quantity)


def evidence_id(key: str, ticker: str | None, as_of: datetime) -> str:
    """e.g. ('analyst.eps_revision_30d', 'NVDA') → ANALYST_EPS_REVISION_30D_NVDA_20260925."""
    base = key.upper().replace(".", "_").replace("/", "_").replace(" ", "_")
    stamp = as_of.strftime("%Y%m%d")
    return f"{base}_{ticker}_{stamp}" if ticker else f"{base}_{stamp}"


class EvidenceBuilder:
    def __init__(self, ticker: str, as_of: datetime) -> None:
        self.ticker = ticker
        self.as_of = as_of
        self._items: dict[str, Evidence] = {}
        self._key_to_id: dict[str, str] = {}

    def add(self, key: str, category: str, label: str, value: float | str | None, source: str, source_ts: datetime | None = None, quality: str = "FRESH", ticker_scoped: bool = True, explicit_id: str | None = None, period: str | None = None, unit: str | None = None) -> str:
        eid = explicit_id or evidence_id(key, self.ticker if ticker_scoped else None, self.as_of)
        if isinstance(value, float):
            value = round(value, 6)
        self._items[eid] = Evidence(eid, category, label, value, source, source_ts, quality, key, self.ticker if ticker_scoped else None, unit or infer_unit(key), period, infer_basis(key))
        self._key_to_id[key] = eid
        return eid

    def id_for(self, key: str) -> str | None:
        return self._key_to_id.get(key)

    def ids_for(self, keys: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(i for k in keys if (i := self._key_to_id.get(k)) is not None)

    def items(self) -> tuple[Evidence, ...]:
        return tuple(self._items.values())
