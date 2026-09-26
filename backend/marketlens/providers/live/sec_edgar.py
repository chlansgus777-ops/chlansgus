"""SEC EDGAR (official, free): universe, company profile, XBRL fundamentals, shares outstanding, Form 4.

Endpoints (SEC requires a descriptive User-Agent with contact info and ≤ 10 requests/second):
- https://www.sec.gov/files/company_tickers_exchange.json        ticker → CIK, exchange
- https://data.sec.gov/submissions/CIK##########.json             SIC code, country, filing forms, Form 4 list
- https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json   financial statement facts
- https://data.sec.gov/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{Y}Q{Q}I.json
                                                                  shares outstanding of ALL filers in one call
Point-in-time: every value keeps the date it was FIRST filed, per field (``field_filed``); restatements
never overwrite the original value.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date, timedelta
from typing import Any
from xml.etree import ElementTree

from marketlens.domain.enums import DataMode, Exchange
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Security
from marketlens.domain.options import OwnershipSnapshot
from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import CompanyProfileExtras, NotSupported, ProviderDataError, ProviderUnavailable
from marketlens.providers.live.http import HttpClient
from marketlens.providers.live.sic import classify_sic

EXCHANGE_MAP = {"Nasdaq": Exchange.NASDAQ, "NYSE": Exchange.NYSE, "NYSE American": Exchange.NYSE_AMERICAN, "NYSE MKT": Exchange.NYSE_AMERICAN}
FOREIGN_FORMS = {"20-F", "40-F", "6-K", "20-F/A", "40-F/A"}

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
    "total_equity": ("StockholdersEquity",),
    "shares_diluted": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
    "inventory": ("InventoryNet",),
}
# total debt = long-term debt (incl. current portion) + short-term borrowings + commercial paper
DEBT_LONG = ("LongTermDebt", "LongTermDebtAndCapitalLeaseObligations")
DEBT_LONG_PARTS = ("LongTermDebtNoncurrent", "LongTermDebtCurrent")
DEBT_SHORT = ("ShortTermBorrowings", "CommercialPaper")
FLOW_FIELDS = {"revenue", "gross_profit", "operating_income", "net_income", "eps_diluted", "operating_cash_flow", "capex", "sbc", "depreciation_amortization", "shares_diluted"}
YTD_FIELDS = {"operating_cash_flow", "capex", "sbc", "depreciation_amortization"}  # cash-flow items are reported year-to-date
QUARTER_MAX_DAYS = 100
ANNUAL_MIN_DAYS = 300


class SecEdgarProvider:
    """Implements UniverseProvider, FundamentalProvider and InsiderProvider (Form 4)."""

    mode = DataMode.LIVE

    def __init__(self, user_agent: str | None, transport: Any = None, rate_per_s: float = 8.0) -> None:
        self.name = "sec-edgar"
        self.configured = bool(user_agent)
        headers = {"User-Agent": user_agent or "", "Accept-Encoding": "gzip, deflate"}
        bucket = TokenBucket(rate_per_s=rate_per_s, capacity=8)  # SEC fair access: ≤ 10 requests/second
        self._www = HttpClient("https://www.sec.gov", headers, bucket=bucket, transport=transport)
        self._data = HttpClient("https://data.sec.gov", headers, bucket=bucket, transport=transport)
        self._cik: dict[str, int] = {}

    def _require(self) -> None:
        if not self.configured:
            raise ProviderUnavailable("SEC_USER_AGENT 미설정 (SEC는 연락처가 포함된 User-Agent를 요구합니다)")

    # ------------------------------------------------------------------ universe
    def list_securities(self, as_of: date | None = None) -> list[Security]:
        """Current listings only (the SEC file has no history). Historical membership is kept by the local
        universe store, which records first-seen / delisted dates across syncs."""
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
            out.append(Security(ticker=ticker, company_name=str(row[idx["name"]]), exchange=exch, sector="Unknown", industry="Unknown", market_cap=None))
        return out

    def cik_for(self, ticker: str) -> int:
        if not self._cik:
            self.list_securities()
        cik = self._cik.get(ticker.upper())
        if cik is None:
            raise NotSupported(f"{ticker}: SEC 티커 목록에 없음")
        return cik

    def company_profile(self, ticker: str) -> dict[str, Any]:
        """SIC-based sector/industry, country and foreign-issuer flag from the submissions endpoint."""
        self._require()
        cik = self.cik_for(ticker)
        sub = self._data.get_json(f"/submissions/CIK{cik:010d}.json")
        return parse_submissions_profile(sub)

    def shares_outstanding_all(self, as_of: date) -> dict[int, tuple[float, date]]:
        """CIK → (shares outstanding, as-of date) from the most recent quarterly frames (one call each)."""
        self._require()
        out: dict[int, tuple[float, date]] = {}
        y, q = as_of.year, (as_of.month - 1) // 3 + 1
        for _ in range(4):
            q -= 1
            if q == 0:
                y, q = y - 1, 4
            try:
                data = self._data.get_json(f"/api/xbrl/frames/dei/EntityCommonStockSharesOutstanding/shares/CY{y}Q{q}I.json")
            except ProviderDataError:
                continue  # frame not published yet
            for r in data.get("data", []):
                try:
                    cik, val, end = int(r["cik"]), float(r["val"]), date.fromisoformat(r["end"])
                except (KeyError, TypeError, ValueError):
                    continue
                if end <= as_of and (cik not in out or end > out[cik][1]):
                    out[cik] = (val, end)
        return out

    # ------------------------------------------------------------------ fundamentals
    def get_quarterly(self, ticker: str) -> list[QuarterlyFinancials]:
        self._require()
        cik = self.cik_for(ticker)
        facts = self._data.get_json(f"/api/xbrl/companyfacts/CIK{cik:010d}.json")
        return parse_company_facts(facts, ticker)

    def get_extras(self, ticker: str) -> CompanyProfileExtras:
        raise NotSupported("SEC XBRL은 업종 KPI(CET1, 점유율 등)를 표준 형태로 제공하지 않음")

    # ------------------------------------------------------------------ insiders (Form 4)
    def get_insider(self, ticker: str, as_of: date | None = None, max_filings: int = 25) -> OwnershipSnapshot:
        self._require()
        cik = self.cik_for(ticker)
        sub = self._data.get_json(f"/submissions/CIK{cik:010d}.json")
        end = as_of or date.today()
        start = end - timedelta(days=90)
        recent = sub.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        txs: list[tuple[str, str, float, str]] = []
        net = 0.0
        seen = 0
        for i, form in enumerate(forms):
            if form != "4":
                continue
            fdate = date.fromisoformat(recent["filingDate"][i])
            if not (start <= fdate <= end):
                continue
            if seen >= max_filings:
                break
            seen += 1
            accn = recent["accessionNumber"][i].replace("-", "")
            doc = re.sub(r"^xsl[^/]+/", "", recent["primaryDocument"][i])
            try:
                xml = self._www.get_text(f"/Archives/edgar/data/{cik}/{accn}/{doc}")
            except ProviderDataError:
                continue
            for t in parse_form4(xml):
                txs.append(t)
                net += t[2] if t[3] == "BUY" else -t[2]
        return OwnershipSnapshot(source="sec-form4", insider_net_buy_value_90d=round(net, 2), insider_transactions=tuple(txs[:50]))


def parse_submissions_profile(sub: dict[str, Any]) -> dict[str, Any]:
    try:
        sic = int(sub.get("sic") or 0) or None
    except (TypeError, ValueError):
        sic = None
    sector, industry = classify_sic(sic)
    forms = set(sub.get("filings", {}).get("recent", {}).get("form", [])[:200])
    biz = (sub.get("addresses") or {}).get("business") or {}
    country_desc = biz.get("stateOrCountryDescription") or ""
    foreign = bool(forms & FOREIGN_FORMS)
    return {
        "sic": sic,
        "sic_description": sub.get("sicDescription"),
        "sector": sector,
        "industry": industry,
        "foreign_issuer": foreign,
        "country": ("US" if not foreign else (country_desc or "Foreign")),
        "entity_type": sub.get("entityType"),
    }


def parse_form4(xml: str) -> list[tuple[str, str, float, str]]:
    """Open-market purchases (P) and sales (S) as (date, role, value USD, BUY/SELL)."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError:
        return []
    rel = root.find(".//reportingOwnerRelationship")
    role = "insider"
    if rel is not None:
        if (rel.findtext("isDirector") or "").strip() in ("1", "true"):
            role = "director"
        if (rel.findtext("isOfficer") or "").strip() in ("1", "true"):
            role = (rel.findtext("officerTitle") or "officer").strip()
    out: list[tuple[str, str, float, str]] = []
    for tx in root.findall(".//nonDerivativeTransaction"):
        code = (tx.findtext("transactionCoding/transactionCode") or "").strip()
        if code not in ("P", "S"):
            continue
        try:
            shares = float(tx.findtext("transactionAmounts/transactionShares/value") or 0)
            price = float(tx.findtext("transactionAmounts/transactionPricePerShare/value") or 0)
        except ValueError:
            continue
        d = (tx.findtext("transactionDate/value") or "").strip()
        out.append((d, role, round(shares * price, 2), "BUY" if code == "P" else "SELL"))
    return out


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
    """Cash-flow items are reported year-to-date; quarter = YTD(n) − YTD(n−1) within one fiscal year.

    A YTD figure is only converted when the previous YTD of the same fiscal year is known (or it is itself
    a single quarter), so a missing Q1 never turns a half-year total into a "quarter"."""
    groups: dict[str, list[tuple[date, float, date]]] = defaultdict(list)
    for it in items:
        if it.get("form") not in ("10-Q", "10-K") or not it.get("start"):
            continue
        groups[it["start"]].append((date.fromisoformat(it["end"]), float(it["val"]), date.fromisoformat(it["filed"])))
    out: dict[date, tuple[float, date]] = {}
    for start_s, rows in groups.items():
        start = date.fromisoformat(start_s)
        first_filed: dict[date, tuple[float, date]] = {}
        for end, val, fd in rows:
            if end not in first_filed or fd < first_filed[end][1]:
                first_filed[end] = (val, fd)
        prev_val: float | None = None
        prev_end: date | None = None
        for end in sorted(first_filed):
            val, fd = first_filed[end]
            if prev_end is None:
                if (end - start).days <= QUARTER_MAX_DAYS:
                    out.setdefault(end, (val, fd))
            elif (end - prev_end).days <= QUARTER_MAX_DAYS and prev_val is not None:
                out.setdefault(end, (val - prev_val, fd))
            prev_val, prev_end = val, end
    return out


def parse_company_facts(facts: dict[str, Any], ticker: str) -> list[QuarterlyFinancials]:
    gaap = facts.get("facts", {}).get("us-gaap")
    if not gaap:
        raise NotSupported(f"{ticker}: us-gaap 재무 없음 (IFRS/외국 발행사 가능성)")
    per_period: dict[date, dict[str, float]] = defaultdict(dict)
    field_filed: dict[date, dict[str, date]] = defaultdict(dict)

    revisions: dict[date, dict[str, list[tuple[date, float]]]] = defaultdict(lambda: defaultdict(list))

    def put(end: date, field_name: str, val: float, fdate: date) -> None:
        """First filing wins the main value; later filings with a different value are kept as revisions
        (restatements, split-adjusted comparatives) — append-only, nothing is overwritten."""
        if field_name not in per_period[end]:
            per_period[end][field_name] = val
            field_filed[end][field_name] = fdate
            return
        if fdate <= field_filed[end][field_name]:
            return
        seen = revisions[end][field_name]
        last = seen[-1][1] if seen else per_period[end][field_name]
        if abs(val - last) > 1e-9 * max(1.0, abs(last)):
            seen.append((fdate, val))

    def earliest(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted((it for it in items if it.get("form") in ("10-Q", "10-K")), key=lambda it: it.get("filed", ""))

    for field_name, concepts in CONCEPTS.items():
        unit_pref = ("USD/shares",) if field_name == "eps_diluted" else ("shares",) if field_name == "shares_diluted" else ("USD",)
        items = _pick_concept(gaap, concepts, unit_pref)
        if field_name in YTD_FIELDS:
            for end, (val, fdate) in _ytd_to_quarters(items).items():
                put(end, field_name, val, fdate)
            continue
        annual: dict[date, tuple[date, float, date]] = {}
        # earliest filing first → point-in-time (later restatements never overwrite the original)
        for item in earliest(items):
            end = date.fromisoformat(item["end"])
            if field_name in FLOW_FIELDS and item.get("start"):
                st = date.fromisoformat(item["start"])
                days = (end - st).days
                if days >= ANNUAL_MIN_DAYS:
                    annual.setdefault(end, (st, float(item["val"]), date.fromisoformat(item["filed"])))
                    continue
                if days > QUARTER_MAX_DAYS:
                    continue  # half-year / nine-month YTD figures
            put(end, field_name, float(item["val"]), date.fromisoformat(item["filed"]))
        if field_name in FLOW_FIELDS and field_name not in ("eps_diluted", "shares_diluted"):
            # Q4 is only reported inside the annual 10-K: Q4 = FY − (Q1 + Q2 + Q3); published at the 10-K date
            for end, (st, fy_val, fdate) in annual.items():
                if field_name in per_period.get(end, {}):
                    continue
                qs = [e for e in per_period if st < e < end and field_name in per_period[e]]
                if len(qs) == 3:
                    put(end, field_name, fy_val - sum(per_period[e][field_name] for e in qs), fdate)

    # total debt from its components (instant values), first filing per period
    comp_vals: dict[date, dict[str, tuple[float, date]]] = defaultdict(dict)
    for names in (DEBT_LONG, DEBT_LONG_PARTS[:1], DEBT_LONG_PARTS[1:], DEBT_SHORT[:1], DEBT_SHORT[1:]):
        for item in earliest(_pick_concept(gaap, names, ("USD",))):
            end = date.fromisoformat(item["end"])
            comp_vals[end].setdefault(names[0], (float(item["val"]), date.fromisoformat(item["filed"])))
    for end, comps in comp_vals.items():
        if end not in per_period:
            continue
        if DEBT_LONG[0] in comps:
            parts = [comps[DEBT_LONG[0]]]
        elif DEBT_LONG_PARTS[0] in comps:
            parts = [comps[DEBT_LONG_PARTS[0]]] + ([comps[DEBT_LONG_PARTS[1]]] if DEBT_LONG_PARTS[1] in comps else [])
        else:
            parts = []
        parts += [comps[k] for k in DEBT_SHORT if k in comps]
        if parts:
            put(end, "total_debt", sum(v for v, _ in parts), max(fd for _, fd in parts))

    # cover-page shares outstanding (dei), attached to the quarter filed on the same date
    dei = facts.get("facts", {}).get("dei", {})
    shares_items = earliest(_pick_concept(dei, ("EntityCommonStockSharesOutstanding",), ("shares",))) if dei else []
    filed_to_period: dict[date, date] = {}
    for end, ff in field_filed.items():
        if "revenue" in ff or "net_income" in ff:
            filed_to_period.setdefault(min(ff.values()), end)
    for it in shares_items:
        fd = date.fromisoformat(it["filed"])
        period = filed_to_period.get(fd)
        if period is not None:
            put(period, "shares_outstanding", float(it["val"]), fd)

    out: list[QuarterlyFinancials] = []
    for end in sorted(per_period):
        vals = dict(per_period[end])
        ff = dict(field_filed[end])
        if "revenue" not in vals and "net_income" not in vals:
            continue
        if "eps_diluted" not in vals and vals.get("net_income") is not None and vals.get("shares_diluted"):
            vals["eps_diluted"] = vals["net_income"] / vals["shares_diluted"]  # derived Q4 EPS
            ff["eps_diluted"] = max(ff.get("net_income", date.min), ff.get("shares_diluted", date.min))
        filed = min(ff.values())
        fields = {k: vals.get(k) for k in list(CONCEPTS) + ["total_debt", "shares_outstanding"]}
        rev = {k: tuple(v) for k, v in revisions.get(end, {}).items() if v}
        out.append(QuarterlyFinancials(period_end=end, filed_date=filed, fiscal_label=end.isoformat(), source="sec-edgar", field_filed=ff, revisions=rev, **fields))
    if not out:
        raise NotSupported(f"{ticker}: 분기 10-Q/10-K 데이터 없음")
    return out[-16:]
