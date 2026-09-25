"""Finnhub (commercial API, requires FINNHUB_API_KEY; respect plan limits and terms).

Implemented: real-time quote, company news, earnings calendar, earnings surprises (history).
Candles / estimates / revisions / options need paid plans → not implemented here (MISSING).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from marketlens.domain.catalysts import CatalystEvent, CatalystType
from marketlens.domain.earnings import EarningsReport
from marketlens.domain.enums import DataMode
from marketlens.domain.market import Bar, Quote
from marketlens.domain.market_calendar import classify_session
from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import NewsItem, NotSupported, ProviderDataError, ProviderUnavailable, ValuationHistory
from marketlens.providers.live.http import HttpClient


class FinnhubProvider:
    mode = DataMode.LIVE

    def __init__(self, api_key: str | None, transport: Any = None) -> None:
        self.name = "finnhub"
        self.configured = bool(api_key)
        self._key = api_key
        self._http = HttpClient("https://finnhub.io/api/v1", bucket=TokenBucket(0.9, 5), transport=transport)

    def _get(self, path: str, **params: Any) -> Any:
        if not self.configured:
            raise ProviderUnavailable("FINNHUB_API_KEY not set")
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
                     open=d.get("o"), high=d.get("h"), low=d.get("l"), previous_close=d.get("pc"))

    def get_daily_bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        raise NotSupported("Finnhub candles require a paid plan; use the Polygon provider for bars")

    # --- NewsProvider
    def get_news(self, since: datetime, tickers: Sequence[str] | None = None) -> list[NewsItem]:
        out: list[NewsItem] = []
        for t in tickers or ():
            rows = self._get("/company-news", symbol=t, **{"from": since.date().isoformat(), "to": datetime.now(tz=timezone.utc).date().isoformat()})
            if not isinstance(rows, list):
                raise ProviderDataError("company-news: malformed payload")
            for r in rows:
                ts = datetime.fromtimestamp(int(r.get("datetime", 0)), tz=timezone.utc)
                if ts < since:
                    continue
                related = tuple(x.strip() for x in str(r.get("related", t)).split(",") if x.strip())
                out.append(NewsItem(f"finnhub-{r.get('id')}", ts, str(r.get("headline", "")), str(r.get("summary", "")), str(r.get("url", "")), str(r.get("source", "finnhub")), "COMMERCIAL", related or (t,)))
        return out

    # --- CalendarProvider
    def get_events(self, start: date, end: date) -> list[CatalystEvent]:
        d = self._get("/calendar/earnings", **{"from": start.isoformat(), "to": end.isoformat()})
        rows = d.get("earningsCalendar")
        if not isinstance(rows, list):
            raise ProviderDataError("earnings calendar: malformed payload")
        return [
            CatalystEvent(f"ER-{r['symbol']}-{r['date']}", CatalystType.EARNINGS, date.fromisoformat(r["date"]), f"{r['symbol']} earnings", (r["symbol"],), 0.9, source=self.name)
            for r in rows if r.get("symbol") and r.get("date")
        ]

    # --- AnalystProvider (partial)
    def get_earnings_history(self, ticker: str) -> list[EarningsReport]:
        rows = self._get("/stock/earnings", symbol=ticker)
        if not isinstance(rows, list):
            raise ProviderDataError("earnings: malformed payload")
        return [
            EarningsReport(report_date=date.fromisoformat(r["period"]), fiscal_label=f"Q{r.get('quarter')} {r.get('year')}", source=self.name,
                           eps_actual=r.get("actual"), eps_consensus=r.get("estimate"))
            for r in rows if r.get("period")
        ]

    def get_estimates(self, ticker: str, as_of: date) -> Any:
        raise ProviderUnavailable("EPS/revenue estimate revisions require a licensed estimates feed (not configured)")

    def get_valuation_history(self, ticker: str, multiple: str) -> ValuationHistory:
        raise ProviderUnavailable("historical valuation series not available from Finnhub free tier")
