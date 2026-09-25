"""FRED (Federal Reserve Bank of St. Louis) macro provider — official aggregation of Fed/Treasury/BLS/BEA.

Requires FRED_API_KEY. Observations are fetched with ``observation_end`` = as_of date so historical
queries do not see later data (vintage-aware ALFRED access is a documented future enhancement).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from marketlens.domain.enums import DataMode, DataQuality
from marketlens.domain.facts import Fact
from marketlens.domain.macro import (
    BRENT, CORE_CPI_YOY, CORE_PCE_YOY, CPI_YOY, FED_FUNDS, GDP_QOQ_SAAR, HY_SPREAD, NASDAQ_COMP,
    PAYROLLS_CHG, PCE_YOY, SPX, UNEMPLOYMENT, US2Y, US10Y, US30Y, USD_INDEX, VIX, WTI, MacroSeries,
)
from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import ProviderDataError, ProviderUnavailable
from marketlens.providers.live.http import HttpClient

# canonical id -> (FRED series id, transform)
FRED_MAP: dict[str, tuple[str, str]] = {
    FED_FUNDS: ("DFF", "level"),
    US2Y: ("DGS2", "level"),
    US10Y: ("DGS10", "level"),
    US30Y: ("DGS30", "level"),
    CPI_YOY: ("CPIAUCSL", "yoy"),
    CORE_CPI_YOY: ("CPILFESL", "yoy"),
    PCE_YOY: ("PCEPI", "yoy"),
    CORE_PCE_YOY: ("PCEPILFE", "yoy"),
    PAYROLLS_CHG: ("PAYEMS", "diff"),
    UNEMPLOYMENT: ("UNRATE", "level"),
    GDP_QOQ_SAAR: ("A191RL1Q225SBEA", "level"),
    USD_INDEX: ("DTWEXBGS", "level"),
    WTI: ("DCOILWTICO", "level"),
    BRENT: ("DCOILBRENTEU", "level"),
    VIX: ("VIXCLS", "level"),
    HY_SPREAD: ("BAMLH0A0HYM2", "level"),
    SPX: ("SP500", "level"),
    NASDAQ_COMP: ("NASDAQCOM", "level"),
}
DAILY = {FED_FUNDS, US2Y, US10Y, US30Y, USD_INDEX, WTI, BRENT, VIX, HY_SPREAD, SPX, NASDAQ_COMP}


class FredMacroProvider:
    mode = DataMode.LIVE

    def __init__(self, api_key: str | None, transport: Any = None) -> None:
        self.name = "fred"
        self.configured = bool(api_key)
        self._key = api_key
        self._http = HttpClient("https://api.stlouisfed.org", bucket=TokenBucket(2.0, 5), transport=transport)

    def _observations(self, fred_id: str, end: date, start: date) -> list[tuple[date, float]]:
        data = self._http.get_json(
            "/fred/series/observations",
            {"series_id": fred_id, "api_key": self._key, "file_type": "json", "observation_start": start.isoformat(), "observation_end": end.isoformat()},
        )
        obs = data.get("observations")
        if not isinstance(obs, list):
            raise ProviderDataError(f"FRED {fred_id}: malformed payload")
        out: list[tuple[date, float]] = []
        for o in obs:
            v = o.get("value")
            if v in (None, ".", ""):
                continue  # FRED uses "." for missing — never interpolated
            out.append((date.fromisoformat(o["date"]), float(v)))
        return out

    def get_series(self, series_ids: Sequence[str], as_of: datetime) -> dict[str, MacroSeries]:
        if not self.configured:
            raise ProviderUnavailable("FRED_API_KEY not set")
        end = as_of.date()
        out: dict[str, MacroSeries] = {}
        for sid in series_ids:
            if sid not in FRED_MAP:
                continue
            fid, transform = FRED_MAP[sid]
            lookback = timedelta(days=500 if transform == "yoy" else 400)
            obs = self._observations(fid, end, end - lookback)
            if not obs:
                continue
            series = [v for _, v in obs]
            d_last = obs[-1][0]
            if transform == "yoy":
                if len(series) < 13:
                    continue
                vals = [(series[i] / series[i - 12] - 1) * 100 for i in range(12, len(series))]
            elif transform == "diff":
                vals = [series[i] - series[i - 1] for i in range(1, len(series))]
            else:
                vals = series
            latest = vals[-1]
            ts = datetime(d_last.year, d_last.month, d_last.day, 21, 0, tzinfo=timezone.utc)
            stale_days = 5 if sid in DAILY else 70 if transform != "level" or sid == GDP_QOQ_SAAR else 45
            quality = DataQuality.FRESH if (end - d_last).days <= stale_days else DataQuality.STALE
            fact = Fact(latest, f"fred:{fid}", ts, datetime.now(tz=timezone.utc), quality, DataMode.LIVE)
            lag = 20 if sid in DAILY else 1
            prev = vals[-1 - lag] if len(vals) > lag else None
            ch = latest - prev if prev is not None else None
            pct = (latest / prev - 1) if prev not in (None, 0) else None
            above = None
            if sid in (SPX, NASDAQ_COMP) and len(vals) >= 200:
                above = latest > sum(vals[-200:]) / 200
            out[sid] = MacroSeries(sid, fact, change_20d=ch, pct_change_20d=pct, above_200d=above)
        return out
