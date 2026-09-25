"""Polygon.io daily bars (requires POLYGON_API_KEY).

The grouped-daily endpoint returns every US stock's bar for one date in a single request, which makes a
whole-market scan feasible; per-ticker aggregates are used for individual history.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from marketlens.domain.enums import DataMode
from marketlens.domain.market import Bar, Quote
from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import NotSupported, ProviderDataError, ProviderUnavailable
from marketlens.providers.live.http import HttpClient


class PolygonProvider:
    mode = DataMode.LIVE

    def __init__(self, api_key: str | None, transport: Any = None, rate_per_s: float = 0.08) -> None:
        self.name = "polygon"
        self.configured = bool(api_key)
        self._key = api_key
        self._http = HttpClient("https://api.polygon.io", bucket=TokenBucket(rate_per_s, 5), transport=transport)

    def _get(self, path: str, **params: Any) -> Any:
        if not self.configured:
            raise ProviderUnavailable("POLYGON_API_KEY not set")
        params["apiKey"] = self._key
        return self._http.get_json(path, params)

    def get_quote(self, ticker: str) -> Quote:
        raise NotSupported("real-time snapshot requires a paid Polygon plan; use Finnhub quotes")

    def get_daily_bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        d = self._get(f"/v2/aggs/ticker/{ticker}/range/1/day/{start.isoformat()}/{end.isoformat()}", adjusted="true", sort="asc", limit=50000)
        return _bars(d.get("results"))

    def get_grouped_daily(self, day: date) -> dict[str, Bar]:
        d = self._get(f"/v2/aggs/grouped/locale/us/market/stocks/{day.isoformat()}", adjusted="true")
        rows = d.get("results") or []
        out: dict[str, Bar] = {}
        for r in rows:
            try:
                out[str(r["T"])] = Bar(day, float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"]))
            except (KeyError, TypeError, ValueError):
                continue
        return out


def _bars(rows: Any) -> list[Bar]:
    from datetime import datetime, timezone

    from marketlens.domain.market_calendar import NY

    if rows is None:
        return []
    if not isinstance(rows, list):
        raise ProviderDataError("aggregates: malformed payload")
    out = []
    for r in rows:
        d = datetime.fromtimestamp(int(r["t"]) / 1000, tz=timezone.utc).astimezone(NY).date()
        out.append(Bar(d, float(r["o"]), float(r["h"]), float(r["l"]), float(r["c"]), float(r["v"])))
    return out
