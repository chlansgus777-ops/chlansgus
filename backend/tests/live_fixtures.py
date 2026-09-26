"""HTTP fixtures shaped exactly like the free LIVE sources (SEC EDGAR, Polygon grouped daily, Finnhub free,
FRED/ALFRED, FINRA consolidated short interest). Used through ``httpx.MockTransport`` so the real provider
code (URL building, parsing, point-in-time handling, failover) runs unchanged — only the network is replaced.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx

from marketlens.domain.market_calendar import add_trading_days, is_trading_day, last_completed_session

UTC = timezone.utc
NOW = datetime(2026, 9, 25, 15, 0, tzinfo=UTC)  # Friday 11:00 ET (regular session)

COMPANIES: dict[str, dict[str, Any]] = {
    "NVDA": {"cik": 1045810, "name": "NVIDIA CORP", "exch": "Nasdaq", "sic": 3674, "shares": 24.4e9, "px": 180.0, "rev": 30e9, "g": 0.10, "margin": 0.55},
    "JPM": {"cik": 19617, "name": "JPMORGAN CHASE & CO", "exch": "NYSE", "sic": 6021, "shares": 2.8e9, "px": 290.0, "rev": 45e9, "g": 0.02, "margin": 0.30},
    "TSM": {"cik": 1046179, "name": "TAIWAN SEMICONDUCTOR MANUFACTURING CO LTD", "exch": "NYSE", "sic": 3674, "shares": 5.18e9, "px": 250.0, "foreign": True},
    "TINY": {"cik": 999001, "name": "TINY SOFTWARE INC", "exch": "Nasdaq", "sic": 7372, "shares": 5e6, "px": 8.0, "rev": 2e6, "g": 0.0, "margin": 0.05},
}
INDEX = {"SPY": 650.0}
ACTIVE_DAYS = 90  # trading days of grouped-daily history available


def trading_days_back(end: date, n: int) -> list[date]:
    out = []
    d = end
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return list(reversed(out))


def _px(base: float, i: int, t: str) -> float:
    return base * (1 + 0.0006 * i + 0.012 * math.sin(i / 5 + len(t)))


def grouped(day: date) -> list[dict[str, Any]]:
    days = trading_days_back(last_completed_session(NOW), ACTIVE_DAYS)
    if day not in days:
        return []
    i = days.index(day)
    rows = []
    for t, c in list(COMPANIES.items()) + [(k, {"px": v}) for k, v in INDEX.items()]:
        close = _px(c["px"], i, t)
        vol = 4e5 if t == "TINY" else 3e7
        rows.append({"T": t, "o": round(close * 0.998, 4), "h": round(close * 1.012, 4), "l": round(close * 0.988, 4), "c": round(close, 4), "v": vol})
    return rows


# ------------------------------------------------------------------------------------------ SEC


def _fact(val: float, end: date, filed: date, form: str, start: date | None = None, fp: str = "Q") -> dict[str, Any]:
    d = {"val": val, "end": end.isoformat(), "filed": filed.isoformat(), "form": form, "fy": end.year, "fp": fp}
    if start is not None:
        d["start"] = start.isoformat()
    return d


def quarters_back(n: int = 9) -> list[tuple[date, date, str]]:
    """(quarter start, quarter end, filing date/form) — calendar fiscal year, 10-Q 35 days / 10-K 50 days after."""
    out = []
    y, q = 2026, 2
    for _ in range(n):
        end = date(y, 3 * q, 30 if q in (2, 3) else 31)
        start = date(y, 3 * q - 2, 1)
        out.append((start, end, "10-K" if q == 4 else "10-Q"))
        q -= 1
        if q == 0:
            y, q = y - 1, 4
    return list(reversed(out))


def filed_for(end: date, form: str) -> date:
    return end + timedelta(days=50 if form == "10-K" else 35)


def companyfacts(t: str) -> dict[str, Any]:
    c = COMPANIES[t]
    if c.get("foreign"):
        # 20-F (IFRS, TWD): annual facts only — the shape SEC companyfacts uses for foreign private issuers
        def fy(concept_vals: dict[int, float], flow: bool = True) -> dict[str, Any]:
            return {"units": {"TWD": [{"val": v, "end": f"{y}-12-31", **({"start": f"{y}-01-01"} if flow else {}), "filed": f"{y + 1}-04-15", "form": "20-F", "fy": y, "fp": "FY"}
                                      for y, v in concept_vals.items()]}}
        return {"cik": c["cik"], "entityName": c["name"], "facts": {"ifrs-full": {
            "Revenue": fy({2024: 2.894e12, 2025: 3.81e12}), "GrossProfit": fy({2024: 1.624e12, 2025: 2.29e12}),
            "ProfitLossFromOperatingActivities": fy({2024: 1.322e12, 2025: 1.86e12}), "ProfitLossAttributableToOwnersOfParent": fy({2024: 1.173e12, 2025: 1.65e12}),
            "CashFlowsFromUsedInOperatingActivities": fy({2024: 1.826e12, 2025: 2.4e12}), "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": fy({2024: 0.956e12, 2025: 1.2e12}),
            "CashAndCashEquivalents": fy({2024: 2.13e12, 2025: 2.5e12}, flow=False), "EquityAttributableToOwnersOfParent": fy({2024: 3.9e12, 2025: 4.9e12}, flow=False),
        }}}
    rev: list[dict[str, Any]] = []
    ni: list[dict[str, Any]] = []
    eps: list[dict[str, Any]] = []
    op: list[dict[str, Any]] = []
    gp: list[dict[str, Any]] = []
    ocf: list[dict[str, Any]] = []
    capex: list[dict[str, Any]] = []
    cash: list[dict[str, Any]] = []
    debt: list[dict[str, Any]] = []
    eq: list[dict[str, Any]] = []
    wds: list[dict[str, Any]] = []
    dei: list[dict[str, Any]] = []
    bank = {k: [] for k in ("loans", "deposits", "goodwill", "intangibles", "provision", "nco")}  # type: ignore[var-annotated]
    fy_prov = fy_nco = 0.0
    ytd_ocf = ytd_capex = 0.0
    fy_rev = fy_ni = fy_op = fy_gp = 0.0
    for k, (start, end, form) in enumerate(quarters_back()):
        filed = filed_for(end, form)
        r = c["rev"] * (1 + c["g"]) ** (k / 4)
        n = r * c["margin"] * 0.6
        o = r * c["margin"] * 0.8
        g = r * min(0.9, c["margin"] + 0.2)
        fy_start = date(end.year, 1, 1)
        if end.month == 3:
            ytd_ocf = ytd_capex = 0.0
            fy_rev = fy_ni = fy_op = fy_gp = 0.0
        ytd_ocf += n * 1.2
        ytd_capex += r * 0.05
        fy_rev, fy_ni, fy_op, fy_gp = fy_rev + r, fy_ni + n, fy_op + o, fy_gp + g
        if form == "10-K":  # Q4 appears only inside the annual report (12-month durations)
            rev.append(_fact(fy_rev, end, filed, form, fy_start, "FY"))
            ni.append(_fact(fy_ni, end, filed, form, fy_start, "FY"))
            op.append(_fact(fy_op, end, filed, form, fy_start, "FY"))
            gp.append(_fact(fy_gp, end, filed, form, fy_start, "FY"))
        else:
            rev.append(_fact(r, end, filed, form, start))
            ni.append(_fact(n, end, filed, form, start))
            op.append(_fact(o, end, filed, form, start))
            gp.append(_fact(g, end, filed, form, start))
            eps.append(_fact(round(n / (c["shares"] * 1.01), 4), end, filed, form, start))
        wds.append(_fact(c["shares"] * 1.01, end, filed, form, start))
        ocf.append(_fact(ytd_ocf, end, filed, form, fy_start, "FY" if form == "10-K" else "Q"))  # year-to-date
        capex.append(_fact(ytd_capex, end, filed, form, fy_start, "FY" if form == "10-K" else "Q"))
        cash.append(_fact(r * 0.8, end, filed, form))
        debt.append(_fact(r * 0.5, end, filed, form))
        eq.append(_fact(r * 3, end, filed, form))
        dei.append(_fact(c["shares"], filed - timedelta(days=5), filed, form))
        if c.get("sic") == 6021:  # bank concepts (us-gaap) as a bank's 10-Q/10-K tag them
            if end.month == 3:
                fy_prov = fy_nco = 0.0
            prov, nco = r * 0.04, r * 0.03
            fy_prov, fy_nco = fy_prov + prov, fy_nco + nco
            bank["loans"].append(_fact(r * 30 * (1 + 0.01 * k), end, filed, form))
            bank["deposits"].append(_fact(r * 50, end, filed, form))
            bank["goodwill"].append(_fact(52e9, end, filed, form))
            bank["intangibles"].append(_fact(3e9, end, filed, form))
            if form == "10-K":
                bank["provision"].append(_fact(fy_prov, end, filed, form, fy_start, "FY"))
                bank["nco"].append(_fact(fy_nco, end, filed, form, fy_start, "FY"))
            else:
                bank["provision"].append(_fact(prov, end, filed, form, start))
                bank["nco"].append(_fact(nco, end, filed, form, start))
    usd = lambda items: {"units": {"USD": items}}  # noqa: E731
    return {"cik": c["cik"], "entityName": c["name"], "facts": {
        "us-gaap": {
            "Revenues": usd(rev), "NetIncomeLoss": usd(ni), "OperatingIncomeLoss": usd(op), "GrossProfit": usd(gp),
            "EarningsPerShareDiluted": {"units": {"USD/shares": eps}},
            "NetCashProvidedByUsedInOperatingActivities": usd(ocf), "PaymentsToAcquirePropertyPlantAndEquipment": usd(capex),
            "CashAndCashEquivalentsAtCarryingValue": usd(cash), "LongTermDebt": usd(debt), "StockholdersEquity": usd(eq),
            "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": wds}},
            **({"LoansAndLeasesReceivableNetReportedAmount": usd(bank["loans"]), "Deposits": usd(bank["deposits"]), "Goodwill": usd(bank["goodwill"]),
                "IntangibleAssetsNetExcludingGoodwill": usd(bank["intangibles"]), "ProvisionForLoanLeaseAndOtherLosses": usd(bank["provision"]),
                "AllowanceForLoanAndLeaseLossesWriteOffsNet": usd(bank["nco"])} if bank["loans"] else {}),
        },
        "dei": {"EntityCommonStockSharesOutstanding": {"units": {"shares": dei}}},
    }}


def submissions(t: str) -> dict[str, Any]:
    c = COMPANIES[t]
    forms = ["20-F", "6-K", "6-K"] if c.get("foreign") else ["10-Q", "4", "10-K", "4", "8-K"]
    dates = ["2026-08-10", "2026-09-10", "2026-08-01", "2026-07-15", "2026-07-28"][: len(forms)]
    items = ["", "", "", "", "2.02,9.01"][: len(forms)]  # the 8-K with Item 2.02 carries the earnings release (Exhibit 99.1)
    return {
        "cik": str(c["cik"]), "name": c["name"], "sic": str(c["sic"]), "sicDescription": "fixture", "entityType": "operating",
        "addresses": {"business": {"stateOrCountryDescription": "Taiwan" if c.get("foreign") else "CA"}},
        "filings": {"recent": {"form": forms, "filingDate": dates, "accessionNumber": [f"0000000000-26-00000{i}" for i in range(len(forms))], "items": items,
                               "acceptanceDateTime": [f"{d}T20:05:00.000Z" for d in dates],
                               "primaryDocument": ["xslF345X05/form4.xml" if f == "4" else "doc.htm" for f in forms]}},
    }


RELEASE_991 = """<html><body><p>NVIDIA Announces Financial Results for Second Quarter Fiscal 2026</p>
<p>Revenue for the second quarter was $30.2 billion.</p><p>Outlook</p>
<p>NVIDIA's outlook for the third quarter of fiscal 2026 is as follows:</p>
<ul><li>Revenue is expected to be $33.0 billion, plus or minus 2%.</li>
<li>GAAP gross margins are expected to be 74.4%, plus or minus 50 basis points.</li></ul></body></html>"""


FORM4 = """<?xml version="1.0"?><ownershipDocument><reportingOwner><reportingOwnerRelationship><isOfficer>1</isOfficer>
<officerTitle>CFO</officerTitle></reportingOwnerRelationship></reportingOwner><nonDerivativeTable><nonDerivativeTransaction>
<transactionDate><value>2026-09-08</value></transactionDate><transactionCoding><transactionCode>S</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>1000</value></transactionShares><transactionPricePerShare><value>175</value></transactionPricePerShare>
</transactionAmounts></nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""


def frames(y: int, q: int) -> dict[str, Any]:
    end = date(y, 3 * q, 30 if q in (2, 3) else 31)
    return {"data": [{"cik": c["cik"], "val": c["shares"], "end": end.isoformat(), "accn": "x"} for c in COMPANIES.values()]}


# ------------------------------------------------------------------------------------------ others


def fred_observations(series_id: str, vintage: date) -> dict[str, Any]:
    monthly = series_id in ("CPIAUCSL", "CPILFESL", "PCEPI", "PCEPILFE", "PAYEMS", "UNRATE")
    quarterly = series_id.startswith("A191")
    base = {"DGS10": 4.2, "DGS2": 3.7, "DGS30": 4.7, "DFF": 4.1, "VIXCLS": 17.0, "BAMLH0A0HYM2": 3.2, "DCOILWTICO": 72.0, "DCOILBRENTEU": 76.0,
            "DTWEXBGS": 120.0, "SP500": 6400.0, "NASDAQCOM": 21000.0, "DEXKOUS": 1385.0, "UNRATE": 4.3, "A191RL1Q225SBEA": 2.0,
            "CPIAUCSL": 320.0, "CPILFESL": 325.0, "PCEPI": 125.0, "PCEPILFE": 124.0, "PAYEMS": 159000.0}.get(series_id, 100.0)
    obs = []
    if monthly or quarterly:
        step = 3 if quarterly else 1
        y, m = vintage.year, vintage.month - 1
        pts = []
        for _ in range(0, 26, step):
            while m <= 0:
                y, m = y - 1, m + 12
            pts.append(date(y, m, 1))
            m -= step
        for i, d in enumerate(reversed(pts)):
            obs.append({"date": d.isoformat(), "value": str(round(base * (1 + 0.0025 * i), 3))})
    else:
        for i, d in enumerate(trading_days_back(vintage - timedelta(days=1), 260)):
            obs.append({"date": d.isoformat(), "value": str(round(base * (1 + 0.0004 * i), 4))})
    return {"observations": obs}


def finnhub(path: str, q: dict[str, list[str]]) -> Any:
    ts = int(NOW.timestamp())
    if path.endswith("/quote"):
        t = q["symbol"][0]
        c = COMPANIES.get(t) or {"px": INDEX.get(t, 100.0)}
        px = _px(c["px"], ACTIVE_DAYS - 1, t) * 1.003
        return {"c": round(px, 2), "t": ts - 30, "o": px, "h": px * 1.01, "l": px * 0.99, "pc": px}
    if path.endswith("/news"):
        assert q.get("category") == ["general"], "market news must use /news?category=general"
        return [
            {"id": 11, "datetime": ts - 3600, "headline": "Treasury yields climb after hot inflation print", "summary": "10-year yield rises.", "url": "https://www.reuters.com/a", "source": "Reuters", "related": ""},
            {"id": 12, "datetime": ts - 7200, "headline": "Stocks to watch this week", "summary": "A round-up of movers.", "url": "https://example.com/b", "source": "Blog", "related": "NVDA,JPM,TSM,AAPL,MSFT,AMZN"},
        ]
    if path.endswith("/company-news"):
        t = q["symbol"][0]
        name = COMPANIES.get(t, {}).get("name", t).split(" ")[0].title()
        return [
            {"id": 100 + len(t), "datetime": ts - 5000, "headline": f"{name} raises full-year outlook", "summary": f"{name} ({t}) lifts guidance.", "url": f"https://www.businesswire.com/{t}", "source": "Business Wire", "related": t},
            {"id": 200 + len(t), "datetime": ts - 6000, "headline": "Five dividend stocks for retirees", "summary": "Income ideas.", "url": "https://example.com/div", "source": "Blog", "related": t},
        ]
    if path.endswith("/calendar/earnings"):
        if "symbol" in q:  # history for one company (real report dates)
            t = q["symbol"][0]
            c = COMPANIES.get(t, {})
            rows = []
            for k, (_s, end, form) in enumerate(quarters_back()):
                if form == "10-K" or "rev" not in c:
                    continue
                r = c["rev"] * (1 + c["g"]) ** (k / 4)
                eps = round(r * c["margin"] * 0.6 / (c["shares"] * 1.01), 4)
                rows.append({"symbol": t, "date": (end + timedelta(days=28)).isoformat(), "quarter": (end.month - 1) // 3 + 1, "year": end.year,
                             "epsActual": eps, "epsEstimate": round(eps * 0.97, 4), "revenueActual": r, "revenueEstimate": r * 0.99})
            return {"earningsCalendar": rows}
        # upcoming reports with consensus (the fields Finnhub's calendar carries for future dates)
        rows = [{"symbol": "NVDA", "date": "2026-11-19", "quarter": 3, "year": 2026, "epsEstimate": 0.47, "revenueEstimate": 34.1e9, "epsActual": None, "revenueActual": None, "hour": "amc"},
                {"symbol": "JPM", "date": add_trading_days(NOW.date(), 12).isoformat(), "quarter": 3, "year": 2026, "epsEstimate": 4.95, "revenueEstimate": 45.8e9, "epsActual": None, "revenueActual": None, "hour": "bmo"}]
        lo, hi = date.fromisoformat(q["from"][0]), date.fromisoformat(q["to"][0])
        return {"earningsCalendar": [r for r in rows if lo <= date.fromisoformat(r["date"]) <= hi]}
    return None


def alphavantage(q: dict[str, list[str]]) -> Any:
    """Shape of Alpha Vantage EARNINGS_ESTIMATES (string values, as the provider returns them)."""
    assert q["function"] == ["EARNINGS_ESTIMATES"]
    sym = q["symbol"][0]
    if sym != "NVDA":
        return {"symbol": sym, "estimates": []}

    def row(d: str, horizon: str, avg: float, ago: tuple[float, float, float, float], rev: float) -> dict[str, str]:
        return {"date": d, "horizon": horizon, "eps_estimate_average": str(avg), "eps_estimate_high": str(round(avg * 1.08, 4)), "eps_estimate_low": str(round(avg * 0.93, 4)),
                "eps_estimate_analyst_count": "42", "eps_estimate_average_7_days_ago": str(ago[0]), "eps_estimate_average_30_days_ago": str(ago[1]),
                "eps_estimate_average_60_days_ago": str(ago[2]), "eps_estimate_average_90_days_ago": str(ago[3]),
                "eps_estimate_revision_up_trailing_7_days": "3", "eps_estimate_revision_down_trailing_7_days": "0",
                "eps_estimate_revision_up_trailing_30_days": "11", "eps_estimate_revision_down_trailing_30_days": "2",
                "revenue_estimate_average": str(rev), "revenue_estimate_high": str(rev * 1.05), "revenue_estimate_low": str(rev * 0.96), "revenue_estimate_analyst_count": "40"}

    return {"symbol": "NVDA", "estimates": [
        row("2026-10-31", "current fiscal quarter", 0.47, (0.465, 0.45, 0.44, 0.43), 34.0e9),
        row("2027-01-31", "next fiscal quarter", 0.51, (0.505, 0.49, 0.48, 0.47), 36.5e9),
        row("2027-01-31", "current fiscal year", 1.82, (1.81, 1.76, 1.72, 1.69), 131e9),
        row("2028-01-31", "next fiscal year", 2.21, (2.2, 2.12, 2.05, 2.0), 158e9),
    ]}


FINRA_SORT_ERROR = ("Sorting is allowed only if all partitions keys are specified in EQUAL CompareFilter."
                    "Partition keys missing or not using EQUAL CompareFilter: settlementDate")


def finra(body: dict[str, Any]) -> Any:
    """Shape and rules of the real FINRA Query API as observed on 2026-09-26 (GitHub runner, public access): a
    sort needs every partition key (settlementDate) in an EQUAL filter, otherwise 400; rows come unordered."""
    equal = {f["fieldName"] for f in body.get("compareFilters", []) if str(f.get("compareType", "")).upper() == "EQUAL"}
    if body.get("sortFields") and "settlementDate" not in equal:
        return (400, {"statusCode": 400, "statusDescription": "Bad Request", "message": FINRA_SORT_ERROR})
    sym = next(f["fieldValue"] for f in body["compareFilters"] if f["fieldName"] == "symbolCode")
    c = COMPANIES.get(sym)
    if c is None:
        return []
    return [{"symbolCode": sym, "settlementDate": "2026-08-15", "currentShortPositionQuantity": c["shares"] * 0.011, "daysToCoverQuantity": 1.3},
            {"symbolCode": sym, "settlementDate": "2026-08-29", "currentShortPositionQuantity": c["shares"] * 0.012, "previousShortPositionQuantity": c["shares"] * 0.011, "daysToCoverQuantity": 1.4}]


def live_transport(seen: list[str] | None = None) -> httpx.MockTransport:
    by_cik = {f"{c['cik']:010d}": t for t, c in COMPANIES.items()}

    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        if seen is not None:
            seen.append(f"{req.method} {url}")
        u = urlsplit(url)
        q = parse_qs(u.query)
        path = u.path
        if "sec.gov" in u.netloc:
            if path.endswith("company_tickers_exchange.json"):
                return httpx.Response(200, json={"fields": ["cik", "name", "ticker", "exchange"], "data": [[c["cik"], c["name"], t, c["exch"]] for t, c in COMPANIES.items()] + [[5, "OTC CO", "OTCX", "OTC"]]})
            if "/frames/dei/EntityCommonStockSharesOutstanding" in path:
                tag = path.rsplit("/", 1)[-1]  # CY2026Q2I.json
                y, qn = int(tag[2:6]), int(tag[7])
                if date(y, 3 * qn, 28) > NOW.date() - timedelta(days=40):
                    return httpx.Response(404)  # frame not complete yet
                return httpx.Response(200, json=frames(y, qn))
            if "/submissions/CIK" in path:
                return httpx.Response(200, json=submissions(by_cik[path.split("CIK")[1][:10]]))
            if "/companyfacts/CIK" in path:
                return httpx.Response(200, json=companyfacts(by_cik[path.split("CIK")[1][:10]]))
            if "/Archives/edgar/data/" in path and path.endswith("/index.json"):
                return httpx.Response(200, json={"directory": {"item": [{"name": "0000000000-26-000004-index.htm"}, {"name": "d8k.htm"}, {"name": "nvda-ex991.htm"}]}})
            if "/Archives/edgar/data/" in path and path.endswith("ex991.htm"):
                return httpx.Response(200, text=RELEASE_991)
            if "/Archives/edgar/data/" in path:
                return httpx.Response(200, text=FORM4)
        if "polygon.io" in u.netloc and path == "/v3/reference/splits":
            # shape of Polygon's reference/splits response (one unrelated split in the window)
            return httpx.Response(200, json={"status": "OK", "results": [{"id": "E1", "ticker": "OTCX", "execution_date": "2026-06-01", "split_from": 1, "split_to": 2}]})
        if "polygon.io" in u.netloc and "/grouped/" in path:
            day = date.fromisoformat(path.rsplit("/", 1)[-1])
            return httpx.Response(200, json={"status": "OK", "resultsCount": 1, "results": grouped(day)})
        if u.netloc == "www.alphavantage.co" and path == "/query":
            return httpx.Response(200, json=alphavantage(q))
        if "finnhub.io" in u.netloc:
            body = finnhub(path, q)
            return httpx.Response(200, json=body) if body is not None else httpx.Response(404)
        if "stlouisfed.org" in u.netloc:
            vintage = date.fromisoformat(q["realtime_end"][0])
            return httpx.Response(200, json=fred_observations(q["series_id"][0], vintage))
        if u.netloc == "ews.fip.finra.org" and path.endswith("/oauth2/access_token"):
            ok = req.headers.get("authorization", "").startswith("Basic ")
            return httpx.Response(200, json={"access_token": "tok-123", "expires_in": 1800}) if ok else httpx.Response(401)
        if "finra.org" in u.netloc and req.method == "POST":
            if req.headers.get("authorization", "").startswith("Basic "):
                return httpx.Response(401)  # client credentials are never valid on the data endpoint
            out = finra(json.loads(req.content))
            if isinstance(out, tuple):
                return httpx.Response(out[0], json=out[1])
            return httpx.Response(200, json=out)
        return httpx.Response(404)

    return httpx.MockTransport(handler)
