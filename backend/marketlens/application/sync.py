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
from marketlens.domain.market_calendar import is_trading_day, last_completed_session, to_ny
from marketlens.providers.contracts import ProviderError

log = logging.getLogger("marketlens.sync")
PROFILE_MAX_AGE = timedelta(days=30)
FUNDAMENTALS_REFRESH = timedelta(days=7)  # look for a new 10-Q/10-K weekly; stored vintages never expire


@dataclass
class SyncReport:
    universe: dict[str, int] = field(default_factory=dict)
    bar_days_loaded: int = 0
    bar_days_missing: int = 0
    bar_days_empty: int = 0  # trading days the provider returned no rows for (outside its history window)
    shares_updated: int = 0
    market_caps: int = 0
    profiles_updated: int = 0
    fundamentals_ingested: int = 0  # SEC quarterly facts fetched this run (bounded, see the manifest)
    fundamentals_failed: int = 0
    fundamentals_pending: int = 0  # liquid names still waiting for their first/refreshed ingestion
    estimate_snapshots: int = 0
    splits_new: int = 0
    bars_split_adjusted: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        """Every wanted price day was loaded and no step failed. A partial sync is NOT scanner-ready."""
        return not self.errors and self.bar_days_loaded + self.bar_days_empty >= self.bar_days_missing


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
            min_market_cap: float = 1e9, min_dollar_volume: float = 2e7, max_fundamentals: int = 150,
            fundamentals_refresh: timedelta = FUNDAMENTALS_REFRESH) -> SyncReport:
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
            empty_s = self.store.get_setting("grouped_empty_days") or ""
            empty = {date.fromisoformat(x) for x in empty_s.split(",") if x}
            have = self.store.stored_days(grouped.name) | empty
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
                else:
                    empty.add(d)
                    rep.bar_days_empty += 1
            if rep.bar_days_empty:
                self.store.set_setting("grouped_empty_days", ",".join(sorted(x.isoformat() for x in empty)))
        # 2b) stock splits (one bulk request) → rescale stored bars fetched before the split
        # Only splits that have already executed (New York date) are fetched and applied: an announced
        # future split must not rescale today's prices.
        ny_today = to_ny(now).date()
        splitter = _find(self.reg, "price", "get_splits")
        if splitter is not None:
            since_s = self.store.get_setting("splits_checked_through")
            since = date.fromisoformat(since_s) - timedelta(days=7) if since_s else today - timedelta(days=3 * 365)
            try:
                rep.splits_new = len(self.store.save_splits(splitter.get_splits(since, ny_today)))
                self.store.set_setting("splits_checked_through", today.isoformat())
            except ProviderError as e:
                rep.errors.append(f"splits: {e}")
        rep.bars_split_adjusted = self.store.adjust_bars_for_splits(ny_today)
        # 2c) consensus snapshot for every upcoming report (Finnhub earnings calendar, a few requests),
        #     once per day — the append-only history MarketLens accumulates its own revisions from
        cal = _find(self.reg, "analyst", "get_calendar_estimates")
        #     (labelled with the New York day it was actually observed on, not the last completed session)
        if cal is not None and self.store.get_setting("estimates_snapshot_day") != ny_today.isoformat():
            try:
                obs = []
                for k in range(4):  # ~100 days ahead in 25-day windows
                    start = ny_today + timedelta(days=25 * k)
                    obs += cal.get_calendar_estimates(start, start + timedelta(days=24), ny_today)
                rep.estimate_snapshots = self.store.save_estimates(obs)
                self.store.set_setting("estimates_snapshot_day", ny_today.isoformat())
            except ProviderError as e:
                rep.errors.append(f"estimates: {e}")
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
        # 5) SEC quarterly fundamentals for the names the scanner can use — here, bounded per run and
        #    recorded in the ingestion manifest, instead of one SEC request per ticker inside every scan
        fund = _find(self.reg, "fundamental", "get_quarterly")
        if fund is not None:
            self._ingest_fundamentals(fund, now, rep, max_fundamentals, fundamentals_refresh, min_market_cap, min_dollar_volume, today)
        log.info("sync finished", extra={"fields": {"bars": rep.bar_days_loaded, "missing": rep.bar_days_missing, "profiles": rep.profiles_updated}})
        return rep

    def _ingest_fundamentals(self, fund: Any, now: Any, rep: SyncReport, budget: int, refresh: timedelta, min_market_cap: float, min_dollar_volume: float, today: date) -> None:
        from marketlens.application.data_access import FUNDAMENTALS, _failure_status
        from marketlens.providers.contracts import NotSupported, RateLimited

        bars = self.store.last_bars_all(today - timedelta(days=40), today)
        manifest = self.store.ingestion_all(FUNDAMENTALS)
        never: list[tuple[float, str]] = []
        stale: list[tuple[Any, str]] = []
        for s in self.store.securities(None):
            b = bars.get(s.ticker, [])
            if s.is_etf or len(b) < 5 or (s.market_cap or 0) < min_market_cap:
                continue
            if sum(x.close * x.volume for x in b[-20:]) / len(b[-20:]) < min_dollar_volume:
                continue
            m = manifest.get(s.ticker)
            if m is not None and m.next_attempt_at is not None and m.next_attempt_at > now:
                continue  # back-off after a failure (NOT_SUPPORTED: 30 days)
            if m is None or m.last_success_at is None:
                never.append((-(s.market_cap or 0), s.ticker))
            elif now - m.last_success_at >= refresh:
                stale.append((m.last_success_at, s.ticker))
        todo = [t for _, t in sorted(never)] + [t for _, t in sorted(stale)]  # first-time names first, largest first
        rep.fundamentals_pending = max(0, len(todo) - budget)
        for t in todo[:budget]:
            try:
                qs = fund.get_quarterly(t)
            except RateLimited as e:
                self.store.record_ingestion(FUNDAMENTALS, t, now, "RATE_LIMITED", str(e))
                rep.errors.append(f"fundamentals: 요청 한도 — 다음 동기화에서 계속 ({e})")
                rep.fundamentals_pending += 1
                break
            except NotSupported as e:
                self.store.record_ingestion(FUNDAMENTALS, t, now, "NOT_SUPPORTED", str(e))
                continue  # not an error of the sync: e.g. a 20-F filer (annual IFRS only)
            except ProviderError as e:
                self.store.record_ingestion(FUNDAMENTALS, t, now, _failure_status(f"{type(e).__name__}: {e}"), str(e))
                rep.fundamentals_failed += 1
                continue
            self.store.save_quarters(t, qs)
            self.store.record_ingestion(FUNDAMENTALS, t, now, "OK", rows=len(qs))
            rep.fundamentals_ingested += 1
