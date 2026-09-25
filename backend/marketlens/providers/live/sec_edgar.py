"""SEC EDGAR (official source): universe and XBRL company facts.

- Universe: https://www.sec.gov/files/company_tickers_exchange.json
- Facts:    https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
SEC requires a descriptive User-Agent with contact info (SEC_USER_AGENT) and ≤10 requests/second.
Foreign private issuers (20-F/40-F, many ADRs) are often not covered quarterly → NotSupported, so a
secondary FundamentalProvider can take over. Point-in-time: each value keeps its ``filed`` date.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from marketlens.domain.enums import DataMode, Exchange
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Security
from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import CompanyProfileExtras, NotSupported, ProviderDataError, ProviderUnavailable
from marketlens.providers.live.http import HttpClient

EXCHANGE_MAP = {"Nasdaq": Exchange.NASDAQ, "NYSE": Exchange.NYSE, "NYSE American": Exchange.NYSE_AMERICAN, "NYSE MKT": Exchange.NYSE_AMERICAN}

# XBRL concept fallbacks (first available wins)
CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet", "RevenuesNetOfInterestExpense"),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "capex": ("PaymentsToAcquirePropertyPlantAndEquipment",),
    "sbc": ("ShareBasedCompensation", "AllocatedShareBasedCompensationExpense"),
    "depreciation_amortization": ("DepreciationDepletionAndAmortization", "DepreciationAndAmortization"),
    "cash": ("CashAndCashEquivalentsAtCarryingValue",),
    "total_debt": ("LongTermDebt", "LongTermDebtNoncurrent"),
    "total_equity": ("StockholdersEquity",),
    "shares_diluted": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    "inventory": ("InventoryNet",),
}
FLOW_FIELDS = {"revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "operating_cash_flow", "capex", "sbc", "depreciation_amortization", "shares_diluted"}
YTD_FIELDS = {"operating_cash_flow", "capex", "sbc", "depreciation_amortization"}  # cash-flow items are reported year-to-date


class SecEdgarProvider:
    """Implements UniverseProvider and FundamentalProvider."""

    mode = DataMode.LIVE

    def __init__(self, user_agent: str | None, transport: Any = None) -> None:
        self.name = "sec-edgar"
        self.configured = bool(user_agent)
        headers = {"User-Agent": user_agent or "", "Accept-Encoding": "gzip, deflate"}
        bucket = TokenBucket(rate_per_s=8, capacity=8)
        self._www = HttpClient("https://www.sec.gov", headers, bucket=bucket, transport=transport)
        self._data = HttpClient("https://data.sec.gov", headers, bucket=bucket, transport=transport)
        self._cik: dict[str, int] = {}

    def _require(self) -> None:
        if not self.configured:
            raise ProviderUnavailable("SEC_USER_AGENT not set (SEC requires a contact User-Agent)")

    def list_securities(self, as_of: date | None = None) -> list[Security]:
        self._require()
        data = self._www.get_json("/files/company_tickers_exchange.json")
        fields = data.get("fields")
        rows = data.get("data")
        if not isinstance(fields, list) or not isinstance(rows, list):
            raise ProviderDataError("unexpected company_tickers_exchange schema")
        idx = {f: i for i, f in enumerate(fields)}
        out: list[Security] = []
        for row in rows:
            exch = EXCHANGE_MAP.get(row[idx["exchange"]] or "")
            if exch is None:
                continue  # OTC / CBOE / unlisted — not in scope
            ticker = str(row[idx["ticker"]]).upper()
            self._cik[ticker] = int(row[idx["cik"]])
            # sector/industry/market cap are NOT in this file → left unknown (must come from another source)
            out.append(Security(ticker=ticker, company_name=str(row[idx["name"]]), exchange=exch, sector="Unknown", industry="Unknown", market_cap=None))
        return out

    def _cik_for(self, ticker: str) -> int:
        if not self._cik:
            self.list_securities()
        cik = self._cik.get(ticker.upper())
        if cik is None:
            raise NotSupported(f"{ticker} not found in SEC ticker map")
        return cik

    def get_quarterly(self, ticker: str) -> list[QuarterlyFinancials]:
        self._require()
        cik = self._cik_for(ticker)
        facts = self._data.get_json(f"/api/xbrl/companyfacts/CIK{cik:010d}.json")
        return parse_company_facts(facts, ticker)

    def get_extras(self, ticker: str) -> CompanyProfileExtras:
        raise NotSupported("SEC XBRL does not provide sector KPIs (CET1, occupancy, …) in a standard form")


def _pick_concept(gaap: dict[str, Any], names: tuple[str, ...], unit_pref: tuple[str, ...]) -> list[dict[str, Any]]:
    for n in names:
        c = gaap.get(n)
        if not c:
            continue
        units = c.get("units", {})
        for u in unit_pref:
            if u in units:
                return list(units[u])
    return []


def _ytd_to_quarters(items: list[dict[str, Any]]) -> dict[date, tuple[float, date]]:
    """Cash-flow items are reported year-to-date; quarter = YTD(n) − YTD(n−1) within a fiscal year."""
    groups: dict[str, list[tuple[date, float, date]]] = defaultdict(list)
    for it in items:
        if it.get("form") not in ("10-Q", "10-K") or not it.get("start"):
            continue
        groups[it["start"]].append((date.fromisoformat(it["end"]), float(it["val"]), date.fromisoformat(it["filed"])))
    out: dict[date, tuple[float, date]] = {}
    for rows in groups.values():
        first_filed: dict[date, tuple[float, date]] = {}
        for end, val, fd in rows:
            if end not in first_filed or fd < first_filed[end][1]:
                first_filed[end] = (val, fd)
        prev = 0.0
        for end in sorted(first_filed):
            val, fd = first_filed[end]
            out.setdefault(end, (val - prev, fd))
            prev = val
    return out


def parse_company_facts(facts: dict[str, Any], ticker: str) -> list[QuarterlyFinancials]:
    gaap = facts.get("facts", {}).get("us-gaap")
    if not gaap:
        raise NotSupported(f"{ticker}: no us-gaap facts (likely IFRS/foreign filer)")
    per_period: dict[date, dict[str, float]] = defaultdict(dict)
    filed: dict[date, date] = {}

    def put(end: date, field_name: str, val: float, fdate: date) -> None:
        if field_name not in per_period[end]:
            per_period[end][field_name] = val
        filed[end] = min(filed.get(end, fdate), fdate)

    for field_name, concepts in CONCEPTS.items():
        unit_pref = ("USD/shares",) if field_name == "eps_diluted" else ("shares",) if field_name == "shares_diluted" else ("USD",)
        items = _pick_concept(gaap, concepts, unit_pref)
        if field_name in YTD_FIELDS:
            for end, (val, fdate) in _ytd_to_quarters(items).items():
                put(end, field_name, val, fdate)
            continue
        annual: dict[date, tuple[date, float, date]] = {}
        # earliest filing first → point-in-time (later restatements never overwrite the original)
        for item in sorted(items, key=lambda it: it.get("filed", "")):
            if item.get("form") not in ("10-Q", "10-K"):
                continue
            end = date.fromisoformat(item["end"])
            if field_name in FLOW_FIELDS and item.get("start"):
                st = date.fromisoformat(item["start"])
                days = (end - st).days
                if days > 300:
                    annual.setdefault(end, (st, float(item["val"]), date.fromisoformat(item["filed"])))
                    continue
                if days > 100:
                    continue  # half-year / nine-month YTD figures
            put(end, field_name, float(item["val"]), date.fromisoformat(item["filed"]))
        if field_name in FLOW_FIELDS and field_name not in ("eps_diluted", "shares_diluted"):
            # Q4 is only reported inside the annual 10-K: Q4 = FY − (Q1 + Q2 + Q3)
            for end, (st, fy_val, fdate) in annual.items():
                if field_name in per_period.get(end, {}):
                    continue
                qs = [e for e in per_period if st < e < end and field_name in per_period[e]]
                if len(qs) == 3:
                    put(end, field_name, fy_val - sum(per_period[e][field_name] for e in qs), fdate)
    out: list[QuarterlyFinancials] = []
    for end in sorted(per_period):
        vals = per_period[end]
        if "revenue" not in vals and "net_income" not in vals:
            continue
        if "eps_diluted" not in vals and vals.get("net_income") is not None and vals.get("shares_diluted"):
            vals["eps_diluted"] = vals["net_income"] / vals["shares_diluted"]  # derived Q4 EPS
        out.append(QuarterlyFinancials(period_end=end, filed_date=filed[end], fiscal_label=end.isoformat(), source="sec-edgar", **{k: vals.get(k) for k in CONCEPTS}))
    if not out:
        raise NotSupported(f"{ticker}: no quarterly 10-Q/10-K data")
    return out[-16:]
