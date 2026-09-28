"""Market data synchronisation into the local point-in-time store (free sources only).

1. Daily bars: Polygon *grouped daily* — ONE request returns every US stock for a date — for each missing
   trading day in the backfill window (bounded per run to respect the free rate limit).
2. Universe: SEC listings → store (new names get ``first_seen``, vanished names get ``delisted_at``;
   renames / reuses / relists through the security master, judged with the bars just stored).
3. Shares outstanding: SEC XBRL frames (one request per quarter for all filers) → market cap.
4. Sector/industry: SEC submissions (SIC code) for liquid, large-enough names lacking a fresh profile.
"""

from __future__ import annotations

import json

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable

from marketlens.application.market_store import MarketStore
from marketlens.application.registry import ProviderRegistry
from marketlens.domain.market_calendar import is_trading_day, last_completed_session, to_ny
from marketlens.providers.contracts import ProviderError

log = logging.getLogger("marketlens.sync")
Progress = Callable[..., None]  # (step, done, total, detail) — see MarketSync.run
FINAL_WITHOUT_DATA = ("NOT_SUPPORTED", "PARSE_GAP")  # manifest statuses that need no further attempt to count as done
PROFILE_MAX_AGE = timedelta(days=30)
FUNDAMENTALS_REFRESH = timedelta(days=7)  # look for a new 10-Q/10-K weekly; stored vintages never expire
# an empty grouped-daily answer for a session this recent is "not published yet", not "outside the provider's history
# window": it is asked again at the next sync instead of being recorded as empty for good (8th evaluation I3)
RECENT_EMPTY = timedelta(days=7)


# calendar days of market-wide daily bars the sync keeps: about 262 sessions — enough for the one-year history
# (240 sessions, readiness "가격 이력(1년)") and the 200-day average. It was 300 days (~206 sessions), so the one-year
# share could never leave 0 % (owner report 2026-09-28).
BACKFILL_DAYS = 380


@dataclass
class SyncReport:
    universe: dict[str, int] = field(default_factory=dict)
    bar_days_loaded: int = 0
    bar_days_missing: int = 0
    bar_days_empty: int = 0  # trading days the provider returned no rows for (outside its history window)
    bar_days_pending: int = 0  # recent sessions the provider has not published yet (asked again next sync)
    shares_updated: int = 0
    shares_looked_up: int = 0  # liquid stocks without a market cap whose shares were read company by company
    shares_pending: int = 0
    market_caps: int = 0
    profiles_updated: int = 0
    fundamentals_ingested: int = 0  # SEC quarterly facts fetched this run (bounded, see the manifest)
    fundamentals_failed: int = 0
    fundamentals_pending: int = 0  # liquid names still waiting for their first/refreshed ingestion
    estimate_snapshots: int = 0
    splits_new: int = 0
    bars_split_adjusted: int = 0
    errors: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # required datasets with no configured provider (readiness.missing_setup)
    failures: list[str] = field(default_factory=list)  # a few per-company failures with their cause (the screen shows them)

    @property
    def complete(self) -> bool:
        """Every wanted price day was loaded, no step failed and nothing required was skipped for lack of a key (a
        skipped step is not a finished one). A partial sync is NOT scanner-ready."""
        return not self.errors and not self.missing and self.bar_days_loaded + self.bar_days_empty + self.bar_days_pending >= self.bar_days_missing


def _find(registry: ProviderRegistry, kind: str, attr: str) -> Any:
    for p in registry.chain(kind).providers:
        if hasattr(p, attr) and getattr(p, "configured", True):
            return p
    return None


class MarketSync:
    def __init__(self, registry: ProviderRegistry, store: MarketStore) -> None:
        self.reg = registry
        self.store = store

    def run(self, now: Any, backfill_days: int = BACKFILL_DAYS, max_bar_calls: int = 30, max_profiles: int = 300,
            min_market_cap: float = 1e9, min_dollar_volume: float = 2e7, max_fundamentals: int = 150, max_share_lookups: int = 300,
            min_price: float = 5.0,
            fundamentals_refresh: timedelta = FUNDAMENTALS_REFRESH, progress: Progress | None = None) -> SyncReport:
        """``progress(step, done, total, detail)`` is called after every stored item (a session's bars, a company's
        profile or filings) with the counts of the WHOLE preparation, not of this bounded round."""
        from marketlens.application.readiness import missing_setup

        _p: Progress = progress or (lambda *_a: None)
        rep = SyncReport(missing=missing_setup(self.reg, sync_only=True))
        today = last_completed_session(now)
        unloaded: set[date] = set()
        # 1) bars via grouped daily (before the universe step, see 2a)
        grouped = _find(self.reg, "price", "get_grouped_daily")
        if grouped is not None:
            empty_s = self.store.get_setting("grouped_empty_days") or ""
            empty = {date.fromisoformat(x) for x in empty_s.split(",") if x}
            have = self.store.grouped_days() | empty  # market-wide sessions only: one ticker's own history is not one
            wanted = []
            window = 0
            d = today
            while d >= today - timedelta(days=backfill_days):
                if is_trading_day(d):
                    window += 1
                    if d not in have:
                        wanted.append(d)
                d -= timedelta(days=1)
            rep.bar_days_missing = len(wanted)
            unloaded = set(wanted)  # sessions without market data yet: no evidence for the universe step (2a)
            done = window - len(wanted)
            stored_now: set[date] = set()
            _p("bars", done, window, "")
            for d in wanted[:max_bar_calls]:  # newest first; older history fills in over later runs
                try:
                    bars = grouped.get_grouped_daily(d)
                except ProviderError as e:
                    rep.errors.append(f"bars {d}: {e}")
                    break
                unloaded.discard(d)
                if bars:
                    self.store.save_grouped(d, bars, grouped.name)
                    rep.bar_days_loaded += 1
                elif d >= today - RECENT_EMPTY:
                    rep.bar_days_pending += 1  # not published yet: asked again next time, not an error
                    continue  # not done: the progress counts it when it is stored
                else:
                    empty.add(d)
                    rep.bar_days_empty += 1
                stored_now.add(d)
                done += 1
                _p("bars", done, window, d.isoformat())
            if rep.bar_days_empty:
                self.store.set_setting("grouped_empty_days", ",".join(sorted(x.isoformat() for x in empty)))
            if rep.bar_days_loaded + rep.bar_days_empty + rep.bar_days_pending >= rep.bar_days_missing and not rep.errors:
                self.store.set_setting("bars_backfill_complete", (today - timedelta(days=backfill_days)).isoformat())
            elif self.store.get_setting("bars_backfill_complete") and any(d < today - RECENT_EMPTY and d not in stored_now for d in wanted):
                # older sessions are still missing (the window only LOOKED loaded when one ticker's own history counted):
                # the per-ticker reads must not be told the market history is complete. A recent session that failed or
                # is not published yet does not clear it.
                self.store.set_setting("bars_backfill_complete", "")
        # 2a) universe — after the day's bars are stored (a name missing from the SEC file while its bars keep
        #     arriving is a data gap, not a delisting) and before splits (rename links decide which bars a split rescales)
        # every step says when it starts, so the screen never shows the last finished count while a long one runs
        # (owner report 2026-09-28: "가격이력 263/263 · 1분 미만" for ten minutes while these steps ran unreported)
        _p("universe", 0, 1, "SEC 종목 목록")
        try:
            secs = self.reg.chain("universe").call("list_securities", None).value
            rep.universe = self.store.sync_universe(secs, today, unloaded)
            _p("universe", 1, 1, "")
        except ProviderError as e:
            rep.errors.append(f"universe: {e}")
        # 2a') what kind of security each ticker is (Nasdaq Trader symbol directory, two small files, once a day): the
        #      readiness denominators count common stocks / ADSs, not preferreds, units, warrants, notes or funds
        directory = getattr(self.reg, "extras", {}).get("symbol_directory")
        if directory is not None and self.store.get_setting("instrument_kinds_day") != today.isoformat():
            try:
                kinds = directory.instrument_kinds()
                self.store.set_setting("instrument_kinds", json.dumps(kinds, separators=(",", ":")))
                self.store.set_setting("instrument_kinds_day", today.isoformat())
            except ProviderError as e:
                rep.errors.append(f"symbol directory: {e}")
        # 2b) stock splits (one bulk request) → rescale stored bars fetched before the split
        # Only splits that have already executed (New York date) are fetched and applied: an announced
        # future split must not rescale today's prices.
        ny_today = to_ny(now).date()
        splitter = _find(self.reg, "price", "get_splits")
        if splitter is not None:
            _p("splits", 0, 1, "주식분할 기록")
            since_s = self.store.get_setting("splits_checked_through")
            since = date.fromisoformat(since_s) - timedelta(days=7) if since_s else today - timedelta(days=3 * 365)
            try:
                rep.splits_new = len(self.store.save_splits(splitter.get_splits(since, ny_today)))
                self.store.set_setting("splits_checked_through", today.isoformat())
            except ProviderError as e:
                rep.errors.append(f"splits: {e}")
        rep.bars_split_adjusted = self.store.adjust_bars_for_splits(ny_today)
        if splitter is not None:
            _p("splits", 1, 1, "")
        # 2c) consensus snapshot for every upcoming report (Finnhub earnings calendar, a few requests),
        #     once per day — the append-only history MarketLens accumulates its own revisions from
        cal = _find(self.reg, "analyst", "get_calendar_estimates")
        #     (labelled with the New York day it was actually observed on, not the last completed session)
        if cal is not None and self.store.get_setting("estimates_snapshot_day") != ny_today.isoformat():
            try:
                obs = []
                for k in range(4):  # ~100 days ahead in 25-day windows
                    _p("estimates", k, 4, f"{25 * k}~{25 * k + 24}일 뒤")
                    start = ny_today + timedelta(days=25 * k)
                    obs += cal.get_calendar_estimates(start, start + timedelta(days=24), ny_today)
                _p("estimates", 4, 4, "")
                rep.estimate_snapshots = self.store.save_estimates(obs)
                self.store.set_setting("estimates_snapshot_day", ny_today.isoformat())
            except ProviderError as e:
                rep.errors.append(f"estimates: {e}")
        # 3) shares outstanding → market caps
        sec = _find(self.reg, "universe", "shares_outstanding_all")
        if sec is not None:
            _p("shares", 0, 1, "발행주식수(SEC)")
            try:
                by_cik = sec.shares_outstanding_all(today)
                # the company's shares go to its FIRST ticker in the SEC file (the primary equity): handing them to every
                # ticker of the CIK gave a bank's ETNs, notes and preferreds the bank's market cap (diagnosis
                # 2026-09-28: BMO's FNGU/BULZ, Huntington's HBANL…); other share classes are left without one
                ticker_by_cik: dict[int, str] = {}
                for t, c in getattr(sec, "_cik", {}).items():
                    ticker_by_cik.setdefault(c, t)
                rep.shares_updated = self.store.set_shares({ticker_by_cik[c]: v for c, v in by_cik.items() if c in ticker_by_cik})
            except ProviderError as e:
                rep.errors.append(f"shares: {e}")
        rep.market_caps = self.store.refresh_market_caps()
        if sec is not None:
            _p("shares", 1, 1, "")
        if sec is not None and hasattr(sec, "shares_outstanding_of"):
            self._fill_missing_shares(sec, rep, now, today, min_dollar_volume, min_price, max_share_lookups, progress=_p)
        # 4) sector profiles for candidates that could pass eligibility
        if sec is not None and hasattr(sec, "company_profile"):
            _p("profiles", 0, 1, "대상 종목 고르는 중")
            bars = self.store.last_bars_all(today - timedelta(days=40), today)
            todo = []
            eligible = 0
            for s in self.store.securities(None):
                b = bars.get(s.ticker, [])
                if len(b) < 5 or (s.market_cap or 0) < min_market_cap:
                    continue
                adv = sum(x.close * x.volume for x in b[-20:]) / len(b[-20:])
                if adv < min_dollar_volume:
                    continue
                eligible += 1
                age = self.store.profile_age(s.ticker)
                if age is None or age > PROFILE_MAX_AGE:
                    todo.append(s.ticker)
            done = eligible - len(todo)
            _p("profiles", done, eligible, "")
            for t in todo[:max_profiles]:
                try:
                    self.store.set_profile(t, sec.company_profile(t))
                    rep.profiles_updated += 1
                    done += 1
                    _p("profiles", done, eligible, t)
                except ProviderError as e:
                    rep.errors.append(f"profile {t}: {e}")
        # 5) SEC quarterly fundamentals for the names the scanner can use — here, bounded per run and
        #    recorded in the ingestion manifest, instead of one SEC request per ticker inside every scan
        fund = _find(self.reg, "fundamental", "get_quarterly")
        if fund is not None:
            self._ingest_fundamentals(fund, now, rep, max_fundamentals, fundamentals_refresh, min_market_cap, min_dollar_volume, today, progress=_p)
        log.info("sync finished", extra={"fields": {"bars": rep.bar_days_loaded, "missing": rep.bar_days_missing, "profiles": rep.profiles_updated}})
        return rep

    def _fill_missing_shares(self, sec: Any, rep: SyncReport, now: Any, today: date, min_dollar_volume: float, min_price: float, limit: int,
                             progress: Progress | None = None) -> None:
        """Liquid stocks (the scanner's price and dollar-volume limits) still without a market cap after the quarterly
        frames: their shares company by company, most liquid first, bounded per round (owner report 2026-09-28: 70 %).
        Every attempt is in the ingestion manifest (dataset 'shares'): a company without the facts is asked again after
        30 days, a failure after the usual back-off — never in a tight loop."""
        from marketlens.providers.contracts import NotSupported, RateLimited
        from marketlens.providers.live.nasdaq_symbols import COMMON_KINDS, canonical

        _p: Progress = progress or (lambda *_a: None)
        _p("share_lookups", 0, 1, "대상 종목 고르는 중")
        recent = self.store.last_bars_all(today - timedelta(days=40), today)
        kinds = self.store.instrument_kinds()
        man = self.store.ingestion_all("shares")
        todo: list[tuple[float, str]] = []
        for s in self.store.securities(None):
            if s.market_cap is not None or s.is_etf or kinds.get(canonical(s.ticker), "unknown") not in COMMON_KINDS:
                continue
            b = recent.get(s.ticker, [])[-20:]
            adv = sum(x.close * x.volume for x in b) / len(b) if b else 0.0
            if adv < min_dollar_volume or b[-1].close < min_price:
                continue
            m = man.get(s.ticker)
            if m is not None and m.next_attempt_at is not None and m.next_attempt_at > now:
                continue
            todo.append((-adv, s.ticker))
        todo.sort()
        rep.shares_pending = max(0, len(todo) - limit)
        got: dict[str, tuple[float, date]] = {}
        batch = todo[:limit]
        for i, (_adv, t) in enumerate(batch):
            _p("share_lookups", i, len(batch), t)
            try:
                got[t] = sec.shares_outstanding_of(t, today)
                self.store.record_ingestion("shares", t, now, "OK", rows=1)
            except RateLimited as e:
                rep.errors.append(f"shares: 요청 한도 — 다음 동기화에서 계속 ({e})")
                break
            except NotSupported as e:
                self.store.record_ingestion("shares", t, now, "NOT_SUPPORTED", str(e))
            except ProviderError as e:
                self.store.record_ingestion("shares", t, now, "FAILED", str(e))
        _p("share_lookups", len(batch), len(batch), "")
        rep.shares_looked_up = len(got)
        if got:
            rep.shares_updated += self.store.set_shares(got)
            rep.market_caps = self.store.refresh_market_caps()

    def _ingest_fundamentals(self, fund: Any, now: Any, rep: SyncReport, budget: int, refresh: timedelta, min_market_cap: float, min_dollar_volume: float, today: date,
                             progress: Progress | None = None) -> None:
        from marketlens.application.data_access import FUNDAMENTALS, _failure_status
        from marketlens.providers.contracts import NotSupported, RateLimited

        bars = self.store.last_bars_all(today - timedelta(days=40), today)
        # a new parser reads tags the old one could not: companies waiting out a PARSE_GAP / NOT_SUPPORTED back-off are
        # asked again at once (owner report: "retry at 2026-10-04" although only an app update could help them)
        version = getattr(fund, "PARSER_VERSION", None)
        if version and self.store.get_setting("sec_parser_version") != version:
            self.store.clear_backoff(FUNDAMENTALS, ("PARSE_GAP", "NOT_SUPPORTED"))
            self.store.set_setting("sec_parser_version", version)
        manifest = self.store.ingestion_all(FUNDAMENTALS)
        never: list[tuple[float, str]] = []
        stale: list[tuple[Any, str]] = []
        eligible = done = 0
        for s in self.store.securities(None):
            b = bars.get(s.ticker, [])
            if s.is_etf or len(b) < 5 or (s.market_cap or 0) < min_market_cap:
                continue
            if sum(x.close * x.volume for x in b[-20:]) / len(b[-20:]) < min_dollar_volume:
                continue
            m = manifest.get(s.ticker)
            eligible += 1
            if m is not None and (m.last_success_at is not None or m.status in FINAL_WITHOUT_DATA):
                done += 1  # read, or known to have no quarterly us-gaap facts / tags the parser reads
            if m is not None and m.next_attempt_at is not None and m.next_attempt_at > now:
                continue  # back-off after a failure (NOT_SUPPORTED: 30 days)
            if m is None or m.last_success_at is None:
                never.append((-(s.market_cap or 0), s.ticker))
            elif now - m.last_success_at >= refresh:
                stale.append((m.last_success_at, s.ticker))
        todo = [t for _, t in sorted(never)] + [t for _, t in sorted(stale)]  # first-time names first, largest first
        rep.fundamentals_pending = max(0, len(todo) - budget)
        first = {t for _, t in never}
        _p: Progress = progress or (lambda *_a: None)
        _p("fundamentals", done, eligible, "")
        for t in todo[:budget]:
            try:
                qs = fund.get_quarterly(t)
            except RateLimited as e:
                self.store.record_ingestion(FUNDAMENTALS, t, now, "RATE_LIMITED", str(e))
                rep.errors.append(f"fundamentals: 요청 한도 — 다음 동기화에서 계속 ({e})")
                rep.fundamentals_pending += 1
                break
            except NotSupported as e:
                # a filer without us-gaap facts (20-F / IFRS) vs a 10-Q filer whose tags the parser does not read:
                # the second is a gap of MarketLens, counted as missing coverage, never excluded from it
                status = _failure_status(f"not supported: {e}")
                self.store.record_ingestion(FUNDAMENTALS, t, now, status, str(e))
                if status == "PARSE_GAP":
                    rep.fundamentals_failed += 1
                if t in first and status in FINAL_WITHOUT_DATA:
                    done += 1
                    _p("fundamentals", done, eligible, t)
                continue
            except ProviderError as e:
                self.store.record_ingestion(FUNDAMENTALS, t, now, _failure_status(f"{type(e).__name__}: {e}"), str(e))
                rep.fundamentals_failed += 1
                sample = getattr(rep, "failures", None)  # a caller's own report object may not carry samples
                if sample is not None and len(sample) < 3:
                    sample.append(f"재무 {t}: {e}"[:200])
                continue
            self.store.save_quarters(t, qs)
            self.store.record_ingestion(FUNDAMENTALS, t, now, "OK", rows=len(qs))
            rep.fundamentals_ingested += 1
            if t in first:
                done += 1
            _p("fundamentals", done, eligible, t)
