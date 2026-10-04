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
        self._breadth_cache: tuple[date, Any] | None = None  # (session, breadth result) — one whole-market read per session
        self.now_fn = now_fn or (lambda: datetime.now(timezone.utc))
        self.live_quote: Callable[[str], Any] | None = None  # set by the service: the real-time feed's price, when fresh
        self.supplemental_news: Callable[[], list[NewsItem]] | None = None  # cached optional news; never waits for HTTP
        # set by the service: ask the real-time feed for many names in ONE request before they are analysed (a scan);
        # one request per name hit the broker's rate limit and left most of a pre-market scan without a price
        self.prefetch_quotes: Callable[[list[str]], None] | None = None

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
        if self.live_quote is not None:  # the broker's real-time price (토스증권), when it answered this name just now
            q = self.live_quote(t)
            if q is not None:
                return Fetched(q, "toss")
        return self._get("price", "price", "get_quote", t, t, cross_check=relative_conflicts(("price",), 0.02))

    def bars(self, t: str, start: date, end: date, fill_gaps: bool = True) -> Fetched:
        """``fill_gaps``: when the stored history misses the requested START and the market sync has not finished its
        backfill yet, ask the provider for the range (independent review F07: one recent bar answered a request for a
        month). Scans pass False — hundreds of per-ticker requests would exhaust the free rate limit; they wait for
        the sync, and readiness says so."""
        stored: list[Bar] = []
        if self.store is not None:
            stored = self.store.bars(t, start, end)
            # the store is authoritative when it covers the requested end (±3 sessions for sync lag) and either its
            # start, or the backfill of the whole market is complete (then an earlier start is outside the stored
            # window by design, and a later first bar means the company was not trading yet)
            if stored and (end - stored[-1].day).days <= 5:
                front_ok = (stored[0].day - start).days <= 5 or bool(self.store.get_setting("bars_backfill_complete"))
                if front_ok or not fill_gaps:
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

    def company_recommendations(self, s: Any, ticker: str, mode: str, before: datetime | None, on: date, limit: int = 500,
                                inclusive: bool = True, exclude_id: int | None = None, light: bool = False) -> list[Any]:
        """The recommendations of the SECURITY that uses ``ticker`` on ``on``, newest first — across renames and
        relistings, never across a reuse or to another share class (round 10 identity invariant). Every "previous",
        "latest" and "history" lookup goes through here; "which security" is MarketStore.security_id."""
        from sqlalchemy import desc, select

        from marketlens.domain.market_calendar import to_ny
        from marketlens.infrastructure.db.models import RecommendationRow as R

        sid = self.security_of(ticker, on, s)
        labels = (self.store.security_labels(sid, s) | {ticker}) if self.store is not None else {ticker}
        # identity first on three light columns, then the full rows (inputs, results, config — hundreds of KB each) only
        # for the ``limit`` that belong to this security (independent review 2026-09-28 F08: the latest ONE loaded every
        # stored analysis). The identity filter stays before the limit: a reused ticker's newer rows are another company.
        q = select(R.id, R.ticker, R.as_of).where(R.ticker.in_(sorted(labels)), R.mode == mode)
        if before is not None:
            q = q.where(R.as_of <= before if inclusive else R.as_of < before)
        if exclude_id is not None:
            q = q.where(R.id != exclude_id)
        light = s.execute(q.order_by(desc(R.as_of), desc(R.id))).all()
        ids = self.securities_of(list({(t, to_ny(a).date()) for _i, t, a in light}), s)
        # a label that belonged to another security then (a reuse, another class) is not this one's history
        keep = [i for i, t, a in light if ids[(t, to_ny(a).date())] == sid][:limit]
        if not keep:
            return []
        q2 = select(R).where(R.id.in_(keep))
        if light:  # a history list: the heavy JSON loads only if read, inside this session
            from marketlens.infrastructure.db.repository import heavy_deferred

            q2 = q2.options(*heavy_deferred())
        full = {r.id: r for r in s.scalars(q2)}
        return [full[i] for i in keep if i in full]

    def security_of(self, ticker: str, on: date, s: Any) -> str:
        return self.securities_of([(ticker, on)], s)[(ticker, on)]

    def securities_of(self, pairs: list[tuple[str, date]], s: Any) -> dict[tuple[str, date], str]:
        """MarketStore.security_ids; without a store (MOCK) the ticker is the security."""
        if self.store is not None:
            return self.store.security_ids(pairs, s)
        return {(t, d): f"sec:{t}" for t, d in pairs}

    def ledger_securities(self, s: Any, rows: list[Any]) -> list[dict[str, Any]]:
        """Trade records grouped by SECURITY — each record belongs to the security that used its ticker on its day
        (resolved when read, so a reuse found later re-attributes it) — with the ticker the security uses today (None:
        its ticker now names another security) and its splits (every key of its line)."""
        ids = self.securities_of([(r.ticker, r.day) for r in rows], s)
        groups: dict[str, dict[str, Any]] = {}
        for r in rows:
            sid = ids[(r.ticker, r.day)]
            g = groups.setdefault(sid, {"security": sid, "rows": [], "last": r})
            g["rows"].append(r)
            if (r.day, r.id) >= (g["last"].day, g["last"].id):
                g["last"] = r
        for sid, g in groups.items():
            if self.store is None:
                g["ticker"], g["splits"] = g["last"].ticker, []
            else:
                g["ticker"], g["splits"] = self.store.current_label(sid, s), self.store.security_splits(sid, s)
        return sorted(groups.values(), key=lambda g: (g["ticker"] is None, g["ticker"] or g["last"].ticker))

    def current_label(self, security: str, fallback: str, s: Any) -> str | None:
        """The ticker the security uses today (MarketStore.current_label); without a store the entered ticker."""
        return self.store.current_label(security, s) if self.store is not None else fallback

    def security_splits(self, security: str, s: Any) -> list[Any]:
        return self.store.security_splits(security, s) if self.store is not None else []

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

            rep = build(t, self.store.estimate_history(t, as_of), as_of, self.store.splits(t))
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
            f = self._get("analyst", "analyst", "get_earnings_history", t, t)
            if fh is not None and sec is None and (f.error or not f.value):
                # the free path needs the SEC release times: say so, instead of a calendar "0 rows" (evaluation 6)
                return Fetched(None, None, f"sec-8k: SEC_USER_AGENT 미설정 → 실적 발표일(8-K)을 확인할 수 없음; {f.error or '실적 캘린더 빈 응답'}")
            return f
        hit = self.cache.get("analyst.earnings_paired", t, self.ttl.get("analyst", timedelta(minutes=5)))
        if hit is not None:
            return hit
        from marketlens.domain.earnings import RELEASE_MAX_DAYS, pair_with_releases

        try:
            rows = fh.get_earnings_surprises(t)
            if hasattr(sec, "filing_times"):
                times, periodic = sec.filing_times(t, rows[0]["period"])
            else:
                times, periodic = sec.earnings_release_times(t, rows[0]["period"]), []
            reps = pair_with_releases(rows, times, f"{fh.name}+sec-8k", periodic)
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
        base = self._get("news", "news", "get_news", key, since, list(tickers) if tickers else None)
        if self.supplemental_news is None:
            return base
        extra = [n for n in self.supplemental_news() if since <= n.published_at <= self.now_fn()
                 and (not tickers or set(tickers).intersection(n.tickers))]
        if not extra:
            return base
        from marketlens.application.issue_engine import dedupe
        rows, _ = dedupe(list(base.value or []) + extra)
        return Fetched(rows, base.provider or "news supplement", None, base.conflicts)

    def macro_snapshot(self, as_of: datetime) -> tuple[MacroSnapshot | None, str | None]:
        f = self.macro(as_of)
        if f.value is None or not f.value:
            return None, f.error or "거시 데이터 없음"
        series = dict(f.value)
        if self.store is not None:  # LIVE: what FRED does not carry, from the app's own daily bars
            series.update(self._market_series(as_of, series))
        return MacroSnapshot(as_of=as_of, series=series), None

    # the indices FRED does not publish (Russell 2000, PHLX Semiconductor; Nasdaq-100 when its series is refused) are
    # read from the ETFs that track them — their 20-day change and 200-day trend are the index's within a few basis
    # points; the LEVEL is the ETF's price and is labelled so (owner 2026-10-04: "나스닥100, 러셀2000 등 없음")
    INDEX_ETFS = {"RUT": "IWM", "SOX": "SOXX", "NDX": "QQQ"}

    def _market_series(self, as_of: datetime, have: dict[str, Any]) -> dict[str, Any]:
        from marketlens.domain.enums import DataMode, DataQuality
        from marketlens.domain.facts import Fact
        from marketlens.domain.macro import BREADTH_ABOVE_200D, MacroSeries
        from marketlens.domain.market_calendar import last_completed_session

        end = last_completed_session(as_of)  # only sessions that had closed at ``as_of`` (no intraday bar, no look-ahead)
        now = datetime.now(tz=timezone.utc)
        out: dict[str, Any] = {}

        def fact(v: float, src: str, d: date, note: str) -> Fact:
            fresh = (end - d).days <= 5
            return Fact(v, src, datetime(d.year, d.month, d.day, tzinfo=timezone.utc), now,
                        DataQuality.FRESH if fresh else DataQuality.STALE, DataMode.LIVE, note=note)

        for sid, etf in self.INDEX_ETFS.items():
            got = have.get(sid)
            if got is not None and got.latest.value is not None:
                continue
            try:
                bars = [b for b in (self.store.bars(etf, end - timedelta(days=420), end) if self.store else []) if b.day <= end]
            except Exception:  # noqa: BLE001 - one missing proxy is that row missing
                continue
            if len(bars) < 21:
                continue
            closes = [b.close for b in bars]
            last = closes[-1]
            above = last > sum(closes[-200:]) / 200 if len(closes) >= 200 else None
            out[sid] = MacroSeries(sid, fact(last, f"etf:{etf}", bars[-1].day, f"{etf} ETF 가격(지수 값 아님) — 변화율·200일선 판단은 지수와 같음"),
                                   change_20d=last - closes[-21], pct_change_20d=last / closes[-21] - 1, above_200d=above)
        if BREADTH_ABOVE_200D not in have:
            b = self._breadth(end)
            if b is not None:
                share, prev, n, d = b
                out[BREADTH_ABOVE_200D] = MacroSeries(BREADTH_ABOVE_200D, fact(share, "calc:breadth", d, f"저장된 일봉으로 계산 — 보통주 {n:,}개 중 200일 이동평균 위 비율"),
                                                      change_20d=None if prev is None else share - prev, pct_change_20d=None)
        return out

    def _breadth(self, end: date) -> tuple[float, float | None, int, date] | None:
        """Share of the stored common stocks closing above their 200-day average on the last session (and 20 sessions
        before), computed once per session — a whole-market read of ~300 days of bars."""
        hit = self._breadth_cache
        if hit is not None and hit[0] == end:
            return hit[1]
        store = self.store
        if store is None:
            return None
        etfs = {s.ticker for s in store.securities(end) if getattr(s, "is_etf", False)}
        allbars = store.last_bars_all(end - timedelta(days=320), end)
        days = sorted({b.day for bs in allbars.values() for b in bs[-25:]})
        if len(days) < 21:
            self._breadth_cache = (end, None)
            return None
        last_day, prev_day = days[-1], days[-21]

        def share_on(day: date) -> tuple[float | None, int]:
            above = n = 0
            for t, bs in allbars.items():
                if t in etfs:
                    continue
                cl = [b.close for b in bs if b.day <= day]
                if len(cl) < 200 or bs[-1].day < last_day - timedelta(days=7) or cl[-1] < 1:
                    continue  # too young, no longer trading, or a penny line
                n += 1
                above += cl[-1] > sum(cl[-200:]) / 200
            return (above / n if n >= 200 else None), n

        share, n = share_on(last_day)
        prev, _ = share_on(prev_day)
        res = (share, prev, n, last_day) if share is not None else None
        self._breadth_cache = (end, res)
        return res


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
