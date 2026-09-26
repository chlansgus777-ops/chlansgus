"""Cached, failure-tolerant access to provider chains.

Every getter returns ``None`` when data is unavailable and records *why* (never substitutes other data).
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from marketlens.application.registry import ProviderRegistry
from marketlens.domain.catalysts import CatalystEvent
from marketlens.domain.earnings import AnalystSnapshot, EarningsReport
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.macro import ALL_SERIES, MacroSnapshot
from marketlens.domain.market import Bar, Quote, Security
from marketlens.domain.options import OptionsSnapshot, OwnershipSnapshot
from marketlens.providers.contracts import NewsItem, ProviderError, RateLimited
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


FUNDAMENTALS = "fundamentals"


def _failure_status(err: str | None) -> str:
    """Manifest status from the router's error text (``AllProvidersFailed`` lists each provider's outcome)."""
    e = (err or "").lower()
    if "ratelimited" in e or "429" in e:
        return "RATE_LIMITED"
    if "not supported" in e and "error:" not in e and "unavailable" not in e and "not configured" not in e:
        if "us-gaap" in e or "ifrs" in e or "20-f" in e:
            return "NOT_SUPPORTED"  # an IFRS / 20-F filer: no quarterly us-gaap facts exist
        return "PARSE_GAP"  # a 10-Q filer whose XBRL tags the parser does not read yet
    return "FAILED"


class DataAccess:
    """Provider access with TTL caching and (optionally) the local point-in-time store in front.

    Reads prefer the store (no API call); provider results are written back to the store."""

    def __init__(self, registry: ProviderRegistry, ttl: dict[str, timedelta], cache: TTLCache | None = None, store: Any = None,
                 now_fn: Callable[[], datetime] | None = None) -> None:
        self.reg = registry
        self.ttl = ttl
        self.cache = cache or TTLCache()
        self.store = store
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))

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

    def peek_quote(self, t: str) -> Fetched | None:
        """The cached quote if one is still within its TTL — never triggers a provider call."""
        return self.cache.get("price.get_quote", t, self.ttl.get("price", timedelta(minutes=5)))

    def quote(self, t: str) -> Fetched:
        return self._get("price", "price", "get_quote", t, t, cross_check=relative_conflicts(("price",), 0.02))

    def bars(self, t: str, start: date, end: date) -> Fetched:
        stored: list[Bar] = []
        if self.store is not None:
            stored = self.store.bars(t, start, end)
            # the store is authoritative when it covers the requested end (±3 sessions for sync lag)
            if stored and (end - stored[-1].day).days <= 5:
                return Fetched(stored, "store")
            # an archived company (ticker later reused: "ABC~111") or a delisted / renamed-away name is not a
            # current provider ticker — asking for it would return another company or nothing
            if "~" in t or self.store.is_active(t) is False:
                return Fetched(stored, "store") if stored else Fetched(None, None, f"{t}: 저장된 가격 없음(보관·상장폐지 종목은 공급자에 요청하지 않음)")
        f = self._get("bars", "price", "get_daily_bars", f"{t}:{start}:{end}", t, start, end)
        if self.store is not None and f.value:
            self.store.save_bars(t, f.value, f.provider or "provider")
        if not f.value and stored:
            return Fetched(stored, "store")  # a failed refresh keeps the stored history
        return f

    def bars_bulk(self, tickers: list[str], start: date, end: date) -> dict[str, list[Bar]]:
        """Bars for many tickers. With the local store this is ONE query for the whole market; without
        it (mock / first run) it falls back to per-ticker provider calls."""
        out: dict[str, list[Bar]] = {}
        if self.store is not None:
            # the market sync fills the store with ONE grouped request per day; a ticker without stored
            # bars is simply not eligible yet — never one provider call per ticker from the scanner
            stored = self.store.last_bars_all(start, end)
            return {t: stored[t] for t in tickers if t in stored and stored[t]}
        for t in tickers:
            if t not in out:
                v = self.bars(t, start, end).value
                if v:
                    out[t] = list(v)
        return out

    def identity_on(self, t: str, d: date, session: Any = None) -> str:
        """Storage key of the company that used ticker ``t`` on day ``d`` (a later reuse archives it)."""
        return self.store.resolve(t, d, session) if self.store is not None else t

    def splits(self, t: str) -> list[Any]:
        """Stock splits recorded by the market sync (LIVE store); MOCK data is generated split-free."""
        return self.store.splits(t) if self.store is not None else []

    def quarters(self, t: str, allow_fetch: bool = True) -> Fetched:
        """LIVE: the store first. ``allow_fetch=False`` (scanner stage 2, hundreds of names) never calls the
        provider — stored data of any retrieval age is used and a name the sync has not ingested yet is
        MISSING with the manifest's reason. A fetch is skipped while the manifest's retry back-off runs,
        and every attempt is recorded in the ingestion manifest."""
        if self.store is None:
            return self._get("fundamentals", "fundamental", "get_quarterly", t, t)
        stored = self.store.quarters(t, self.ttl.get("fundamentals", timedelta(days=1)))
        if stored:
            return Fetched(stored, "store")
        man = self.store.ingestion(FUNDAMENTALS, t)
        if not allow_fetch:
            anyage = self.store.quarters(t, None)
            if anyage:
                return Fetched(anyage, "store")
            why = f"최근 수집 실패({man.status}: {man.error})" if man is not None and man.status != "OK" else "아직 수집되지 않음 — 다음 동기화에서 수집"
            return Fetched(None, None, f"저장된 재무 없음: {why}")
        now = self.now_fn()
        if man is not None and man.next_attempt_at is not None and man.next_attempt_at > now:
            anyage = self.store.quarters(t, None)
            if anyage:
                return Fetched(anyage, "store")
            return Fetched(None, None, f"재시도 대기({man.next_attempt_at.isoformat(timespec='minutes')}까지): {man.status} {man.error or ''}".strip())
        # a manifest-tracked call is never answered from the TTL cache: every recorded attempt is a real request
        try:
            r = self.reg.chain("fundamental").call("get_quarterly", t)
            f = Fetched(r.value, r.provider, None, r.conflicts)
        except ProviderError as e:
            f = Fetched(None, None, str(e))
        if f.value:
            self.store.save_quarters(t, f.value)
            self.store.record_ingestion(FUNDAMENTALS, t, now, "OK", rows=len(f.value))
        else:
            self.store.record_ingestion(FUNDAMENTALS, t, now, _failure_status(f.error), f.error)
            anyage = self.store.quarters(t, None)
            if anyage:  # a failed refresh keeps the (point-in-time) data already stored
                return Fetched(anyage, "store", None)
        return f

    def annuals(self, t: str) -> Fetched:
        """IFRS annual statements of 20-F filers (ANNUAL_ONLY; no quarterly XBRL exists for them)."""
        return self._get("fundamentals", "fundamental", "get_annual_ifrs", t, t)

    def extras(self, t: str) -> Fetched:
        return self._get("fundamentals", "fundamental", "get_extras", t, t)

    def estimates(self, t: str, as_of: date) -> Fetched:
        """LIVE: consensus + revisions from the stored free-provider snapshots (never a provider call per
        ticker here). MOCK: the mock analyst provider."""
        if self.store is not None:
            from marketlens.application.estimate_book import build

            rep = build(t, self.store.estimate_history(t, as_of), as_of)
            if rep.snapshot is None:
                return Fetched(None, None, "; ".join(rep.notes) or "추정치 없음")
            conflicts = [f"analyst:{rep.cross.status} {rep.cross.detail}"] if rep.cross.status in ("DATA_CONFLICT", "SEVERE_DATA_CONFLICT") else []
            snap = rep.snapshot
            if rep.cross.status == "SEVERE_DATA_CONFLICT":
                # the providers disagree by more than the severe threshold: the EPS consensus and everything
                # derived from it is excluded (never averaged, never one side picked); the decision engine
                # additionally blocks new buying (HardVeto.SEVERE_ESTIMATE_CONFLICT)
                from dataclasses import replace

                snap = replace(snap, forward_eps=None, forward_eps_growth=None, forward_eps_basis=None, eps_revision_7d=None, eps_revision_30d=None,
                               eps_revision_60d=None, eps_revision_90d=None, cross_check=f"{snap.cross_check} → EPS 추정치 제외")
            return Fetched(snap, snap.source, None, conflicts)
        return self._get("analyst", "analyst", "get_estimates", f"{t}:{as_of}", t, as_of)

    def prefetch_guidance(self, tickers: Sequence[str], day: date) -> dict[str, str]:
        """SEC 8-K (Item 2.02) press releases of the final candidates → deterministic guidance extraction.
        Each ticker is checked at most once per day; each filing is extracted once (append-only)."""
        from marketlens.domain.guidance import extract, html_to_text

        out: dict[str, str] = {}
        if self.store is None:
            return out
        sec = next((p for p in self.reg.chain("fundamental").providers if hasattr(p, "earnings_releases") and getattr(p, "configured", True)), None)
        if sec is None:
            return {t: "SEC 공급자 없음" for t in tickers}
        for t in tickers:
            key = f"guidance_checked:{t}"
            if self.store.get_setting(key) == day.isoformat():
                out[t] = "오늘 확인함"
                continue
            try:
                rel = sec.earnings_releases(t, date.fromordinal(day.toordinal() - 200))
                n = sum(self.store.save_guidance(t, r["accession"], r["filed_at"], r["url"], extract(html_to_text(r["text"]))) for r in rel)
                out[t] = f"보도자료 {len(rel)}건, 새 항목 {n}"
                self.store.set_setting(key, day.isoformat())
            except ProviderError as e:
                out[t] = f"실패: {e}"
        return out

    def prefetch_estimates(self, tickers: Sequence[str], day: date, budget: int, ttl_days: int) -> dict[str, str]:
        """Alpha Vantage consensus for the final candidates only, within the daily free budget; a snapshot
        younger than ``ttl_days`` is reused. Returns {ticker: outcome} for the scan report."""
        out: dict[str, str] = {}
        if self.store is None:
            return out
        av = next((p for p in self.reg.chain("analyst").providers if hasattr(p, "get_estimate_observations") and getattr(p, "configured", False)), None)
        if av is None:
            return {t: "ALPHAVANTAGE_API_KEY 없음(BLOCKED_BY_CREDENTIAL)" for t in tickers}
        key = f"av_calls:{day.isoformat()}"
        used = int(self.store.get_setting(key) or 0)
        for t in tickers:
            last = self.store.last_estimate_day(t, "alphavantage")
            if last is not None and (day - last).days < ttl_days:
                out[t] = f"캐시 사용({last.isoformat()})"
                continue
            if used >= budget:
                out[t] = "일일 무료 한도 도달 → 다음 날"
                continue
            used += 1
            self.store.set_setting(key, str(used))
            try:
                self.store.save_estimates(av.get_estimate_observations(t, day))
                out[t] = "OK"
            except ProviderError as e:
                out[t] = f"실패: {e}"
                if isinstance(e, RateLimited):
                    break
        return out

    def earnings(self, t: str) -> Fetched:
        """LIVE free path: Finnhub ``/stock/earnings`` (actual vs consensus EPS per fiscal period, no date) paired
        with the SEC 8-K Item 2.02 acceptance times (the real announcement). The provider chain's earnings
        history (a calendar with real report dates) is used when that path is not available or finds nothing."""
        fh = next((p for p in self.reg.chain("analyst").providers if hasattr(p, "get_earnings_surprises") and getattr(p, "configured", False)), None)
        sec = next((p for p in self.reg.chain("fundamental").providers if hasattr(p, "earnings_release_times") and getattr(p, "configured", True)), None)
        if fh is None or sec is None:
            return self._get("analyst", "analyst", "get_earnings_history", t, t)
        hit = self.cache.get("analyst.earnings_paired", t, self.ttl.get("analyst", timedelta(minutes=5)))
        if hit is not None:
            return hit
        from marketlens.domain.earnings import RELEASE_MAX_DAYS, pair_with_releases

        try:
            rows = fh.get_earnings_surprises(t)
            times = sec.earnings_release_times(t, rows[0]["period"])
            reps = pair_with_releases(rows, times, f"{fh.name}+sec-8k")
            why = "" if reps else f"{t}: 실적 {len(rows)}건 중 {RELEASE_MAX_DAYS}일 안의 8-K 발표(Item 2.02)와 짝지어진 것 없음"
        except ProviderError as e:
            reps, why = [], str(e)
        if reps:
            f = Fetched(reps, f"{fh.name}+sec-8k")
        else:
            f = self._get("analyst", "analyst", "get_earnings_history", t, t)
            if f.error or not f.value:
                f = Fetched(None, None, f"{why}; {f.error or '실적 캘린더 빈 응답'}")
        self.cache.put("analyst.earnings_paired", t, f)
        return f

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
