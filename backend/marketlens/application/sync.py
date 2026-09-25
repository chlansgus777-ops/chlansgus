"""Market data synchronisation into the local point-in-time store (free sources only).

1. Universe: SEC listings → store (new names get ``first_seen``, vanished names get ``delisted_at``).
2. Daily bars: Polygon *grouped daily* — ONE request returns every US stock for a date — for each missing
   trading day in the backfill window (bounded per run to respect the free rate limit).
3. Shares outstanding: SEC XBRL frames (one request per quarter for all filers) → market cap.
4. Sector/industry: SEC submissions (SIC code) for liquid, large-enough names lacking a fresh profile.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from marketlens.application.market_store import MarketStore
from marketlens.application.registry import ProviderRegistry
from marketlens.domain.market_calendar import is_trading_day, last_completed_session
from marketlens.providers.contracts import ProviderError

log = logging.getLogger("marketlens.sync")
PROFILE_MAX_AGE = timedelta(days=30)


@dataclass
class SyncReport:
    universe: dict[str, int] = field(default_factory=dict)
    bar_days_loaded: int = 0
    bar_days_missing: int = 0
    shares_updated: int = 0
    market_caps: int = 0
    profiles_updated: int = 0
    errors: list[str] = field(default_factory=list)


def _find(registry: ProviderRegistry, kind: str, attr: str) -> Any:
    for p in registry.chain(kind).providers:
        if hasattr(p, attr) and getattr(p, "configured", True):
            return p
    return None


class MarketSync:
    def __init__(self, registry: ProviderRegistry, store: MarketStore) -> None:
        self.reg = registry
        self.store = store

    def run(self, now: Any, backfill_days: int = 300, max_bar_calls: int = 30, max_profiles: int = 300,
            min_market_cap: float = 1e9, min_dollar_volume: float = 2e7) -> SyncReport:
        rep = SyncReport()
        today = last_completed_session(now)
        # 1) universe
        try:
            secs = self.reg.chain("universe").call("list_securities", None).value
            rep.universe = self.store.sync_universe(secs, today)
        except ProviderError as e:
            rep.errors.append(f"universe: {e}")
        # 2) bars via grouped daily
        grouped = _find(self.reg, "price", "get_grouped_daily")
        if grouped is not None:
            have = self.store.stored_days(grouped.name)
            wanted = []
            d = today
            while d >= today - timedelta(days=backfill_days):
                if is_trading_day(d) and d not in have:
                    wanted.append(d)
                d -= timedelta(days=1)
            rep.bar_days_missing = len(wanted)
            for d in wanted[:max_bar_calls]:  # newest first; older history fills in over later runs
                try:
                    bars = grouped.get_grouped_daily(d)
                except ProviderError as e:
                    rep.errors.append(f"bars {d}: {e}")
                    break
                if bars:
                    self.store.save_grouped(d, bars, grouped.name)
                    rep.bar_days_loaded += 1
        # 3) shares outstanding → market caps
        sec = _find(self.reg, "universe", "shares_outstanding_all")
        if sec is not None:
            try:
                by_cik = sec.shares_outstanding_all(today)
                ticker_by_cik: dict[int, str] = {}
                for t, c in getattr(sec, "_cik", {}).items():
                    ticker_by_cik.setdefault(c, t)
                rep.shares_updated = self.store.set_shares({ticker_by_cik[c]: v for c, v in by_cik.items() if c in ticker_by_cik})
            except ProviderError as e:
                rep.errors.append(f"shares: {e}")
        rep.market_caps = self.store.refresh_market_caps()
        # 4) sector profiles for candidates that could pass eligibility
        if sec is not None and hasattr(sec, "company_profile"):
            bars = self.store.last_bars_all(today - timedelta(days=40), today)
            todo = []
            for s in self.store.securities(None):
                b = bars.get(s.ticker, [])
                if len(b) < 5 or (s.market_cap or 0) < min_market_cap:
                    continue
                adv = sum(x.close * x.volume for x in b[-20:]) / len(b[-20:])
                age = self.store.profile_age(s.ticker)
                if adv >= min_dollar_volume and (age is None or age > PROFILE_MAX_AGE):
                    todo.append(s.ticker)
            for t in todo[:max_profiles]:
                try:
                    self.store.set_profile(t, sec.company_profile(t))
                    rep.profiles_updated += 1
                except ProviderError as e:
                    rep.errors.append(f"profile {t}: {e}")
        log.info("sync finished", extra={"fields": {"bars": rep.bar_days_loaded, "missing": rep.bar_days_missing, "profiles": rep.profiles_updated}})
        return rep
