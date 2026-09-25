"""Cached, failure-tolerant access to provider chains.

Every getter returns ``None`` when data is unavailable and records *why* (never substitutes other data).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Sequence

from marketlens.application.registry import ProviderRegistry
from marketlens.domain.catalysts import CatalystEvent
from marketlens.domain.earnings import AnalystSnapshot, EarningsReport
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.macro import ALL_SERIES, MacroSnapshot
from marketlens.domain.market import Bar, Quote, Security
from marketlens.domain.options import OptionsSnapshot, OwnershipSnapshot
from marketlens.providers.contracts import NewsItem, ProviderError
from marketlens.providers.router import relative_conflicts

log = logging.getLogger("marketlens.data")


@dataclass
class Fetched:
    value: Any
    provider: str | None
    error: str | None = None
    conflicts: list[str] = field(default_factory=list)


class TTLCache:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._d: dict[tuple[str, str], tuple[float, Fetched]] = {}
        self._clock = clock
        self._lock = threading.Lock()

    def get(self, kind: str, key: str, ttl: timedelta) -> Fetched | None:
        with self._lock:
            hit = self._d.get((kind, key))
            if hit and self._clock() - hit[0] <= ttl.total_seconds():
                return hit[1]
            return None

    def put(self, kind: str, key: str, value: Fetched) -> None:
        with self._lock:
            self._d[(kind, key)] = (self._clock(), value)


class DataAccess:
    """Provider access with TTL caching and (optionally) the local point-in-time store in front.

    Reads prefer the store (no API call); provider results are written back to the store."""

    def __init__(self, registry: ProviderRegistry, ttl: dict[str, timedelta], cache: TTLCache | None = None, store: Any = None) -> None:
        self.reg = registry
        self.ttl = ttl
        self.cache = cache or TTLCache()
        self.store = store

    def _get(self, kind: str, chain: str, method: str, key: str, *args: Any, cross_check: Any = None) -> Fetched:
        ttl = self.ttl.get(kind, timedelta(minutes=5))
        hit = self.cache.get(chain + "." + method, key, ttl)
        if hit is not None:
            return hit
        try:
            r = self.reg.chain(chain).call(method, *args, cross_check=cross_check)
            f = Fetched(r.value, r.provider, None, r.conflicts)
        except ProviderError as e:
            f = Fetched(None, None, str(e))
        self.cache.put(chain + "." + method, key, f)
        return f

    def securities(self, as_of: date | None = None) -> Fetched:
        if self.store is not None:
            stored = self.store.securities(as_of)
            if stored:
                return Fetched(stored, "store")
        return self._get("universe", "universe", "list_securities", str(as_of), as_of)

    def quote(self, t: str) -> Fetched:
        return self._get("price", "price", "get_quote", t, t, cross_check=relative_conflicts(("price",), 0.02))

    def bars(self, t: str, start: date, end: date) -> Fetched:
        if self.store is not None:
            stored = self.store.bars(t, start, end)
            # the store is authoritative when it covers the requested end (±3 sessions for sync lag)
            if stored and (end - stored[-1].day).days <= 5:
                return Fetched(stored, "store")
        f = self._get("bars", "price", "get_daily_bars", f"{t}:{start}:{end}", t, start, end)
        if self.store is not None and f.value:
            self.store.save_bars(t, f.value, f.provider or "provider")
        return f

    def bars_bulk(self, tickers: list[str], start: date, end: date) -> dict[str, list[Bar]]:
        """Bars for many tickers. With the local store this is ONE query for the whole market; without
        it (mock / first run) it falls back to per-ticker provider calls."""
        out: dict[str, list[Bar]] = {}
        if self.store is not None:
            stored = self.store.last_bars_all(start, end)
            out = {t: stored[t] for t in tickers if t in stored and stored[t]}
        for t in tickers:
            if t not in out:
                v = self.bars(t, start, end).value
                if v:
                    out[t] = list(v)
        return out

    def quarters(self, t: str) -> Fetched:
        if self.store is not None:
            stored = self.store.quarters(t, self.ttl.get("fundamentals", timedelta(days=1)))
            if stored:
                return Fetched(stored, "store")
        f = self._get("fundamentals", "fundamental", "get_quarterly", t, t)
        if self.store is not None and f.value:
            self.store.save_quarters(t, f.value)
        return f

    def extras(self, t: str) -> Fetched:
        return self._get("fundamentals", "fundamental", "get_extras", t, t)

    def estimates(self, t: str, as_of: date) -> Fetched:
        return self._get("analyst", "analyst", "get_estimates", f"{t}:{as_of}", t, as_of)

    def earnings(self, t: str) -> Fetched:
        return self._get("analyst", "analyst", "get_earnings_history", t, t)

    def valuation_history(self, t: str, multiple: str) -> Fetched:
        return self._get("analyst", "analyst", "get_valuation_history", f"{t}:{multiple}", t, multiple)

    def options(self, t: str) -> Fetched:
        return self._get("options", "options", "get_options", t, t)

    def short_interest(self, t: str) -> Fetched:
        return self._get("analyst", "short_interest", "get_short_interest", t, t)

    def insider(self, t: str) -> Fetched:
        return self._get("fundamentals", "insider", "get_insider", t, t)

    def macro(self, as_of: datetime) -> Fetched:
        return self._get("macro", "macro", "get_series", as_of.strftime("%Y%m%d%H"), list(ALL_SERIES), as_of)

    def events(self, start: date, end: date) -> Fetched:
        return self._get("macro", "calendar", "get_events", f"{start}:{end}", start, end)

    def news(self, since: datetime, tickers: Sequence[str] | None) -> Fetched:
        key = f"{since:%Y%m%d%H}:{','.join(sorted(tickers)) if tickers else '*'}"
        return self._get("news", "news", "get_news", key, since, list(tickers) if tickers else None)

    def macro_snapshot(self, as_of: datetime) -> tuple[MacroSnapshot | None, str | None]:
        f = self.macro(as_of)
        if f.value is None or not f.value:
            return None, f.error or "거시 데이터 없음"
        return MacroSnapshot(as_of=as_of, series=f.value), None


# typed aliases for readability in the scanner
QuoteT = Quote
BarsT = list[Bar]
QuartersT = list[QuarterlyFinancials]
EstimatesT = AnalystSnapshot
EarningsT = list[EarningsReport]
EventsT = list[CatalystEvent]
NewsT = list[NewsItem]
OptionsT = OptionsSnapshot
OwnershipT = OwnershipSnapshot
SecuritiesT = list[Security]
