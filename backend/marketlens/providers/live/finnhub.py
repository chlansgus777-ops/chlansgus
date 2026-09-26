"""Finnhub (free tier; requires FINNHUB_API_KEY; respect plan limits and terms).

Implemented: quote, market-wide news, company news, earnings calendar, earnings history (actual report
dates with EPS/revenue actual vs estimate from the earnings calendar).
Not available on the free tier → NotSupported (falls through / MISSING, never an error storm):
daily candles, estimate revisions, valuation history.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from marketlens.domain.catalysts import CatalystEvent, CatalystType
from marketlens.domain.earnings import EarningsReport
from marketlens.domain.enums import DataMode
from marketlens.domain.estimates import EstimateObservation
from marketlens.domain.market import Bar, Quote
from marketlens.domain.market_calendar import classify_session
from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import NewsItem, NotSupported, ProviderDataError, ProviderUnavailable, ValuationHistory
from marketlens.providers.live.http import HttpClient

WIRE_SOURCES = ("reuters", "bloomberg", "associated press", "ap news", "dow jones", "marketwatch", "cnbc", "wsj", "financial times", "barron")
OFFICIAL_SOURCES = ("sec.gov", "businesswire", "business wire", "globenewswire", "prnewswire", "pr newswire", "federalreserve", "treasury.gov")


def source_type(source: str, url: str) -> str:
    s = f"{source} {url}".lower()
    if any(k in s for k in OFFICIAL_SOURCES):
        return "OFFICIAL"  # issuer press releases / regulators
    if any(k in s for k in WIRE_SOURCES):
        return "WIRE"
    return "COMMERCIAL"


class FinnhubProvider:
    mode = DataMode.LIVE

    def __init__(self, api_key: str | None, transport: Any = None, realtime: bool = False, rate_per_s: float = 0.9) -> None:
        self.name = "finnhub"
        self.configured = bool(api_key)
        self._key = api_key
        self._realtime = realtime  # only claim real-time quotes when the user's plan guarantees it
        self._http = HttpClient("https://finnhub.io/api/v1", bucket=TokenBucket(rate_per_s, 5), transport=transport)

    def _get(self, path: str, **params: Any) -> Any:
        if not self.configured:
            raise ProviderUnavailable("FINNHUB_API_KEY 미설정")
        params["token"] = self._key
        return self._http.get_json(path, params)

    # --- PriceProvider
    def get_quote(self, ticker: str) -> Quote:
        d = self._get("/quote", symbol=ticker)
        c, t = d.get("c"), d.get("t")
        if not c or not t:
            raise ProviderDataError(f"{ticker}: empty quote")
        ts = datetime.fromtimestamp(int(t), tz=timezone.utc)
        return Quote(ticker=ticker, price=float(c), timestamp=ts, session=classify_session(ts), source=self.name, mode=DataMode.LIVE,
                     open=d.get("o"), high=d.get("h"), low=d.get("l"), previous_close=d.get("pc"), is_realtime=self._realtime)

    def get_daily_bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        raise NotSupported("Finnhub 무료 요금제는 일봉 미제공 → Polygon 사용")

    # --- NewsProvider
    def _items(self, rows: Any, since: datetime, default_tickers: tuple[str, ...]) -> list[NewsItem]:
        if not isinstance(rows, list):
            raise ProviderDataError("news: malformed payload")
        out: list[NewsItem] = []
        for r in rows:
            ts = datetime.fromtimestamp(int(r.get("datetime", 0)), tz=timezone.utc)
            if ts < since:
                continue
            related = tuple(x.strip().upper() for x in str(r.get("related", "")).split(",") if x.strip()) or default_tickers
            src, url = str(r.get("source", "finnhub")), str(r.get("url", ""))
            out.append(NewsItem(f"finnhub-{r.get('id')}", ts, str(r.get("headline", "")), str(r.get("summary", "")), url, src, source_type(src, url), related))
        return out

    def get_news(self, since: datetime, tickers: Sequence[str] | None = None) -> list[NewsItem]:
        if not tickers:
            # market-wide news (a single call) — never silently return an empty list
            return self._items(self._get("/news", category="general"), since, ())
        out: list[NewsItem] = []
        for t in tickers:
            rows = self._get("/company-news", symbol=t, **{"from": since.date().isoformat(), "to": datetime.now(tz=timezone.utc).date().isoformat()})
            out.extend(self._items(rows, since, (t,)))
        return out

    # --- CalendarProvider
    def get_events(self, start: date, end: date) -> list[CatalystEvent]:
        d = self._get("/calendar/earnings", **{"from": start.isoformat(), "to": end.isoformat()})
        rows = d.get("earningsCalendar")
        if not isinstance(rows, list):
            raise ProviderDataError("earnings calendar: malformed payload")
        return [
            CatalystEvent(f"ER-{r['symbol']}-{r['date']}", CatalystType.EARNINGS, date.fromisoformat(r["date"]), f"{r['symbol']} 실적발표", (r["symbol"],), 0.9, source=self.name)
            for r in rows if r.get("symbol") and r.get("date")
        ]

    def get_calendar_estimates(self, start: date, end: date, observed_on: date) -> list[EstimateObservation]:
        """Consensus EPS/revenue for upcoming reports of every company in one request. Stored daily, these
        snapshots become MarketLens's own revision history (the calendar itself has no history)."""
        d = self._get("/calendar/earnings", **{"from": start.isoformat(), "to": end.isoformat()})
        rows = d.get("earningsCalendar")
        if not isinstance(rows, list):
            raise ProviderDataError("earnings calendar: malformed payload")
        out: list[EstimateObservation] = []
        for r in rows:
            sym, rd = r.get("symbol"), r.get("date")
            if not sym or not rd or (r.get("epsEstimate") is None and r.get("revenueEstimate") is None):
                continue
            if r.get("epsActual") is not None:
                continue  # already reported — an actual, not a consensus
            try:
                report = date.fromisoformat(rd)
                y, q = int(r["year"]), int(r["quarter"])
            except (KeyError, TypeError, ValueError):
                continue
            out.append(EstimateObservation(ticker=str(sym).upper(), provider=self.name, period=f"FQ{y}Q{q}", period_type="quarter", period_end=None,
                                           observed_on=observed_on, eps=_num(r.get("epsEstimate")), revenue=_num(r.get("revenueEstimate")),
                                           horizon="upcoming report", report_date=report))
        return out

    # --- AnalystProvider (partial)
    def get_earnings_history(self, ticker: str) -> list[EarningsReport]:
        """Uses the earnings calendar so ``report_date`` is the real announcement date (the /stock/earnings
        endpoint only has the fiscal period end, which must never be treated as the report date)."""
        return self.earnings_window(ticker, 800)

    def earnings_window(self, ticker: str, days: int) -> list[EarningsReport]:
        today = datetime.now(tz=timezone.utc).date()
        start = today - timedelta(days=days)
        d = self._get("/calendar/earnings", symbol=ticker, **{"from": start.isoformat(), "to": today.isoformat()})
        rows = d.get("earningsCalendar")
        if not isinstance(rows, list):
            raise ProviderDataError("earnings history: malformed payload")
        if not rows:
            # a listed company always reported within ~2 years: an empty answer is missing data, never "no earnings"
            raise ProviderDataError(f"earnings history: {ticker} 응답 0건 ({start}~{today})")
        out = []
        for r in rows:
            if not r.get("date") or r.get("epsActual") is None and r.get("revenueActual") is None:
                continue
            out.append(EarningsReport(report_date=date.fromisoformat(r["date"]), fiscal_label=f"Q{r.get('quarter')} {r.get('year')}", source=self.name,
                                      eps_actual=r.get("epsActual"), eps_consensus=r.get("epsEstimate"),
                                      revenue_actual=r.get("revenueActual"), revenue_consensus=r.get("revenueEstimate")))
        return sorted(out, key=lambda x: x.report_date)

    def get_earnings_surprises(self, ticker: str) -> list[dict[str, Any]]:
        """``/stock/earnings`` (free: the last four quarters): fiscal period end, actual and consensus EPS. It has
        NO announcement date — the caller pairs each period with the SEC 8-K release time; the period end is
        never used as a report date. (The free earnings calendar returned nothing for a symbol, even for the
        last 35 days — live-verify run 36252886492.)"""
        d = self._get("/stock/earnings", symbol=ticker)
        if not isinstance(d, list):
            raise ProviderDataError("earnings surprises: malformed payload")
        out = []
        for r in d:
            try:
                period = date.fromisoformat(str(r["period"]))
            except (KeyError, TypeError, ValueError):
                continue
            if r.get("actual") is None:
                continue
            out.append({"period": period, "actual": _num(r.get("actual")), "estimate": _num(r.get("estimate")), "quarter": r.get("quarter"), "year": r.get("year")})
        if not out:
            raise ProviderDataError(f"earnings surprises: {ticker} 응답 0건")
        return sorted(out, key=lambda x: x["period"])

    def get_estimates(self, ticker: str, as_of: date) -> Any:
        raise NotSupported("추정치 리비전은 유료 데이터 → 미제공(MISSING)")

    def get_valuation_history(self, ticker: str, multiple: str) -> ValuationHistory:
        raise NotSupported("과거 밸류에이션 시계열 미제공(MISSING)")


def _num(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
