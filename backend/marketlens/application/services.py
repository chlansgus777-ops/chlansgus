"""MarketLens application service: the single entry point used by the API, CLI and workers."""

from __future__ import annotations

import json

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from sqlalchemy.orm import Session, sessionmaker

from marketlens import __version__
from marketlens.application.codec import decode, encode
from marketlens.application.committee.orchestrator import Committee, CommitteeResult
from marketlens.application.data_access import DataAccess
from marketlens.application.evaluation_service import rec_session_day
from marketlens.application.graph_seed import GraphSeed
from marketlens.application.issue_engine import news_for_ticker
from marketlens.application.market_store import MarketStore
from marketlens.application.pipeline import AnalysisInputs, AnalysisResult
from marketlens.application.registry import ProviderRegistry, build_registry
from marketlens.application.replay import ReplayOutcome, config_snapshot, replay
from marketlens.application.scanner import NEWS_LOOKBACK, ScanContext, ScanResult, Scanner
from marketlens.application.theses import ThesisBook
from marketlens.config import AGENT_PROMPT_VERSION, CONFIG_DIR, SCHEMA_VERSION, ModelConfig, Settings, code_version, load_model_config
from marketlens.domain.enums import BULLISH_ACTIONS, Action, DataMode
from marketlens.domain.freshness import PlanCheck, RecommendationFreshness, recommendation_freshness
from marketlens.domain.corporate_actions import ShareBasis, analysis_basis, encoded_split_keys, share_multiplier
from marketlens.domain.market_calendar import UTC, to_ny
from marketlens.domain.paper import position_notional
from marketlens.domain.ledger import LedgerError, LedgerNotFound, Trade, check_delete, check_new, positions
from marketlens.domain.portfolio import Holding, Portfolio
from marketlens.domain.sizing import effective_size, recommendation_size_cap, tightest
from marketlens.domain.what_changed import AnalysisDigest
from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import CommitteeRow, FactorSnapshotRow, PaperPositionRow, RecommendationRow, ScanRunRow
from marketlens.infrastructure.health import HealthRegistry
from marketlens.infrastructure.logging import Event, log_event, redact_text
from marketlens.providers.llm.base import LLMProvider, UnavailableLLM

log = logging.getLogger("marketlens.service")
DEFAULT_CASH = 100_000.0
COMMITTEE_OK = ("COMPLETED", "PARTIAL", "REUSED")
CONTEXT_MAX_AGE = timedelta(minutes=30)  # the issues screen rebuilds an older shared market context


PLAN_PRICE_FIELDS = ("ideal_entry", "acceptable_low", "acceptable_high", "max_buy", "add_zone_low", "add_zone_high", "stop", "target1", "target2",
                     "support_used", "resistance_used")


def stored_size_class(r: AnalysisResult, committee: CommitteeResult | None) -> str | None:
    """The one size limit stored with a recommendation (independent review 2026-09-28 F01/F05): the decision's own
    limit (vetoes, unknown sector, portfolio review) and, when the committee's result is used, its portfolio manager's
    — whichever is smaller. The buy amount and the paper position read it back through ``recommendation_size_cap``."""
    ok = committee is not None and committee.status in COMMITTEE_OK
    return tightest(r.decision.size_limit, r.portfolio_review.size_cap.value if r.portfolio_review else None, committee.size_class if ok and committee else None)


def ledger_trade(r: Any) -> Trade:
    """A stored trade record as the ledger's Trade (the one conversion)."""
    return Trade(int(r.id), r.day, r.kind, float(r.quantity), float(r.price), float(r.fees), float(r.amount), float(r.split_from), float(r.split_to))


def build_llm(settings: Settings) -> LLMProvider:
    p = settings.llm_provider
    if settings.mode == DataMode.MOCK and p in ("mock", "none", ""):
        from marketlens.providers.llm.mock_llm import MockLLMProvider

        return MockLLMProvider()
    if p == "anthropic":
        from marketlens.providers.llm.anthropic_provider import AnthropicProvider

        prov = AnthropicProvider(settings.anthropic_api_key, settings.fast_model, settings.deep_model)
        return prov if prov.available else UnavailableLLM("ANTHROPIC_API_KEY 미설정")
    if p in ("openai", "openai_compatible"):
        from marketlens.providers.llm.openai_compat import OpenAICompatibleProvider

        return OpenAICompatibleProvider(settings.openai_base_url, settings.openai_api_key, settings.fast_model, settings.deep_model, name=p)
    if p == "mock":
        # a mock LLM in LIVE mode would mix mock output with live data → refused
        return UnavailableLLM("LIVE 모드에서는 모의(mock) LLM을 사용할 수 없음")
    return UnavailableLLM("LLM_PROVIDER 미설정")


@dataclass
class ScanSummary:
    scan_id: int
    as_of: datetime
    candidates: int
    committee_run: int
    paper_opened: int


def scan_coverage(result: ScanResult, llm: Mapping[str, Any]) -> dict[str, Any]:
    """How much of the market this scan actually judged, and why the rest was left out: the universe, the names
    excluded by reason, the names analysed, how many of those lacked data for a decision, and the AI cost."""
    from collections import Counter

    stages = [{"stage": st.stage, "in": st.input_count, "out": st.output_count, "note": st.note} for st in result.stages]
    universe = result.stages[0].input_count if result.stages else len(result.excluded) + len(result.candidates)
    analysed = len(result.candidates)
    insufficient = sum(1 for r in result.candidates if r.decision.action == Action.DATA_INSUFFICIENT)
    missing_fields: Counter[str] = Counter()
    for r in result.candidates:
        for name, q in r.data_quality.fields:
            if q.value == "MISSING":
                missing_fields[name] += 1
    def reason_group(txt: str) -> str:  # "주가 4.03 < 5.0" and "가격 이력이 오래됨(마지막 …)" are one reason each
        if txt.startswith("주가 "):
            return "주가 기준 미달"
        return txt.split("(", 1)[0].strip()

    reasons = Counter(reason_group(v) for v in result.excluded.values())
    deep = result.stages[-1].input_count if result.stages else analysed
    return {
        "universe": universe, "excluded": len(result.excluded), "deep_analysed": deep, "analysed": analysed,
        "data_insufficient": insufficient, "data_insufficient_rate": round(insufficient / analysed, 4) if analysed else None,
        "missing_by_field": {k: {"count": v, "rate": round(v / analysed, 4)} for k, v in missing_fields.most_common()} if analysed else {},
        "excluded_by_reason": dict(reasons.most_common(8)), "stages": stages,
        "llm": {"calls": llm.get("calls", 0), "estimated_cost_usd": llm.get("estimated_cost_usd", 0.0), "cost_complete": llm.get("cost_complete", True),
                "input_tokens": llm.get("input_tokens", 0), "output_tokens": llm.get("output_tokens", 0)},
    }


class MixedEnvironmentError(RuntimeError):
    pass


def assert_db_environment(settings: Settings) -> None:
    """Pre-start check (before migrations/server): the database must belong to ``settings.mode``."""
    import os

    from sqlalchemy import inspect

    from marketlens.infrastructure.db.session import make_engine, make_session_factory

    eng = make_engine(settings.database_url)
    try:
        if "app_settings" not in inspect(eng).get_table_names():
            return  # a new database is claimed by the first service that opens it
        with make_session_factory(eng)() as s:
            have = repo.get_setting(s, "db_environment", "")
    finally:
        eng.dispose()
    if have and have != settings.mode.value and os.environ.get("MARKETLENS_ALLOW_MIXED_DB") != "1":
        raise MixedEnvironmentError(f"이 데이터베이스는 {have} 모드용입니다. {settings.mode.value} 모드로 열 수 없습니다 — 데이터 혼합 방지. "
                                    f"MARKETLENS_DATABASE_URL을 모드별로 분리하세요.")


class ScanRefused(RuntimeError):
    """A scan request that must not run now (another scan, or the data preparation, is running)."""


class MarketLensService:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session], registry: ProviderRegistry | None = None, llm: LLMProvider | None = None, cfg: ModelConfig | None = None, now_fn: Any = None, use_store: bool | None = None) -> None:
        self.settings = settings
        self.sf = session_factory
        self.health = registry.health if registry else HealthRegistry(on_transition=self._on_health)
        self.registry = registry or build_registry(settings, self.health)
        self.base_cfg = cfg or load_model_config()
        self.llm = llm or build_llm(settings)
        self.seed = GraphSeed()
        self.theses = ThesisBook()
        self._now = now_fn or (lambda: datetime.now(tz=UTC))
        # LIVE: provider → local point-in-time store → scanner. MOCK data is generated in memory.
        store_on = (self.registry.mode == DataMode.LIVE) if use_store is None else use_store
        self.store = MarketStore(session_factory, self.registry.mode.value) if store_on else None
        self.data = DataAccess(self.registry, self.base_cfg.cache_ttl, store=self.store, now_fn=self.now)
        self._lock = threading.Lock()
        self._sync_lock = threading.Lock()  # the background preparation job (taken by start_sync, freed by the job)
        self._ledger_lock = threading.Lock()  # one trade-record change at a time (check and write together)
        self._sync_progress: dict[str, Any] = {}  # live counts of the running preparation (MarketSync progress callback)
        self._sync_run = threading.Lock()  # one sync_market() at a time, whoever calls it (9th evaluation H5)
        # scan and data preparation exclude each other BOTH ways, checked and taken in one step under this guard
        # (independent review 2026-09-28 F07: a sync could start while a scan read the same prices and filings)
        self._jobs_guard = threading.Lock()
        self.last_scan_context: ScanContext | None = None
        self._ctx_build = threading.Lock()  # one market-context rebuild at a time (market_context)
        self._ctx_swap = threading.Lock()
        self._master: tuple[date, float, dict[str, Any]] | None = None  # (day, loaded at, ticker → Security)
        self._check_db_environment()
        self.quotes, self._quote_stream = self._build_quotes()

    # ------------------------------------------------------------------ live quotes (independent of analysis)
    def _build_quotes(self) -> tuple[Any, Any]:
        """One app-wide latest-quote state. LIVE with a Finnhub key: the official trade stream (one connection) plus
        one REST snapshot per newly subscribed name; otherwise snapshots only (never labelled real-time)."""
        from marketlens.application.live_quotes import FinnhubStream, QuoteHub

        st = self.settings
        key = getattr(st, "finnhub_api_key", None)
        enabled = getattr(st, "live_quotes", True)
        streaming = False
        if self.mode == DataMode.LIVE and key and enabled:
            try:
                import websockets.sync.client  # noqa: F401

                streaming = True
            except ImportError:
                log.warning("quote stream disabled: the 'websockets' package is not installed")
        coverage = ("Finnhub 체결 스트림(무료 계정) — 거래소 범위·통합시세(SIP) 여부는 공식 문서로 확인되지 않아 '미국 통합 시세'로 표시하지 않음"
                    if streaming else "스트림 없음 — REST 스냅샷(지연 가능)만 표시" if self.mode == DataMode.LIVE else "모의 데이터(MOCK) — 실제 시세 아님")
        hub = QuoteHub(source="finnhub" if streaming else ("finnhub" if self.mode == DataMode.LIVE else "mock"),
                       max_symbols=getattr(st, "quote_stream_max_symbols", 50), coverage_ko=coverage, streaming=streaming,
                       snapshot=lambda t: self.data.quote(t).value, pinned_loader=self._quote_pins,
                       snapshot_every=timedelta(minutes=5) if streaming else timedelta(minutes=2))
        stream = FinnhubStream(hub, key) if streaming and key else None
        return hub, stream

    def _quote_pins(self) -> dict[str, list[str]]:
        with self.sf() as s:
            return {"holdings": sorted({h.ticker for h in self.portfolio(s).holdings}), "watchlist": [w.ticker for w in repo.watchlist(s)]}

    def start_quotes(self) -> None:
        if not getattr(self.settings, "live_quotes", True):
            return
        self.quotes.start()
        if self._quote_stream is not None:
            self._quote_stream.start()

    def stop_quotes(self) -> None:
        if self._quote_stream is not None:
            self._quote_stream.stop()
        self.quotes.stop()

    def _check_db_environment(self) -> None:
        """A database belongs to one data mode. MOCK and LIVE rows share tables (securities are keyed by
        ticker), so opening a LIVE database in MOCK mode (or the reverse) is refused instead of silently
        mixing synthetic and real data. Override only with MARKETLENS_ALLOW_MIXED_DB=1 (tests/migration)."""
        import os

        with self.sf() as s:
            have = repo.get_setting(s, "db_environment", "")
            if not have:
                repo.set_setting(s, "db_environment", self.mode.value)
                s.commit()
            elif have != self.mode.value and os.environ.get("MARKETLENS_ALLOW_MIXED_DB") != "1":
                raise MixedEnvironmentError(f"이 데이터베이스는 {have} 모드용입니다. {self.mode.value} 모드로 열 수 없습니다 — 데이터 혼합 방지. "
                                            f"MARKETLENS_DATABASE_URL을 모드별로 분리하세요.")

    # ------------------------------------------------------------------ helpers
    def now(self) -> datetime:
        return self._now()

    @property
    def mode(self) -> DataMode:
        return self.registry.mode

    def _on_health(self, name: str, before: Any, after: Any) -> None:
        log_event(log, Event.PROVIDER_RECOVERED if after.value == "HEALTHY" else Event.PROVIDER_FAILED, provider=name, before=before.value, after=after.value)

    def model_config(self) -> ModelConfig:
        """Production weights may have been promoted by calibration; otherwise the TOML defaults apply."""
        with self.sf() as s:
            prod = repo.production_model(s)
        if prod is None:
            return self.base_cfg
        return load_model_config(CONFIG_DIR, weights_override=dict(prod.weights), scoring_version_override=prod.version)

    def portfolio(self, s: Session) -> Portfolio:
        """The user's holdings, one per SECURITY (MarketStore.security_id). A security whose trade records include a
        buy is computed from them (domain.ledger — splits, sales and dividends by one rule); otherwise its entered line
        is used, put on today's share basis. Anything left out or overridden is said in the notes, never dropped
        silently. Every holding is labelled with the ticker the security uses today."""
        cash = float(repo.get_setting(s, "portfolio_cash", str(DEFAULT_CASH)) or DEFAULT_CASH)
        today = to_ny(self.now()).date()
        hs: list[Holding] = []
        notes: list[str] = []
        ledgers = {g["security"]: g for g in self.ledger(s, today)}

        def profile(ticker: str) -> tuple[str, tuple[str, ...], float]:
            # the latest market context, else the stored security master: right after a restart there is no context yet,
            # and a holding read as sector "Unknown" let the first scan ignore the sector limit (review 2026-09-28 F02)
            ctx = self.last_scan_context
            sec = ctx.securities.get(ticker) if ctx is not None and ticker in (ctx.securities or {}) else self.security_master(today).get(ticker)
            exp = self.seed.macro_exposure(sec) if sec else None
            return (sec.sector if sec else "Unknown", ("AI",) if exp and exp.ai >= 0.4 else (), exp.rates if exp else 0.0)

        def decides(g: dict[str, Any] | None) -> bool:  # records with a buy decide the holding (a lone dividend does not)
            return g is not None and g["position"] is not None and any(t.kind == "BUY" for t in g["trades"])

        rows = repo.holdings(s)
        entered = {r.ticker: to_ny(r.updated_at if r.updated_at.tzinfo is not None else r.updated_at.replace(tzinfo=UTC)).date() for r in rows}
        sids = self.data.securities_of([(r.ticker, entered[r.ticker]) for r in rows], s)
        manual: dict[str, Any] = {}
        for r in sorted(rows, key=lambda r: entered[r.ticker]):
            sid = sids[(r.ticker, entered[r.ticker])]
            if sid in manual:
                notes.append(f"{manual[sid].ticker}: 같은 종목의 수동 입력 줄이 둘 — 나중에 저장한 {r.ticker} 줄을 씁니다")
            manual[sid] = r
        for sid, r in manual.items():
            if decides(ledgers.get(sid)):
                notes.append(f"{r.ticker}: 수동 입력 줄({r.quantity:g}주)은 쓰지 않습니다 — 이 종목의 보유는 거래 기록 기준입니다")
                continue
            label = self.data.current_label(sid, r.ticker, s)
            if label is None:
                notes.append(f"{r.ticker}: 입력한 종목의 티커가 지금은 다른 종목의 것입니다(티커 재사용) — 가격이 없어 평가에서 빠짐. 줄을 고치세요")
                continue
            # a holding entered before a split is on the old share basis: the split multiplies the quantity and divides
            # the average cost, as the broker does, so it is valued with today's split-adjusted closes (9th evaluation H4)
            f = share_multiplier(self.data.security_splits(sid, s), ShareBasis(entered[r.ticker]), today) or 1.0
            sector, themes, rates = profile(label)
            hs.append(Holding(label, r.quantity * f, r.cost_basis / f, sector, themes, rates, split_adjusted=f))
        for sid, g in ledgers.items():
            label = g["ticker"] or g["last"].ticker
            pos = g["position"]
            if pos is None:
                fallback = " 수동 입력 줄을 대신 씁니다." if sid in manual else " 이 종목은 보유와 한도 계산에서 빠져 있으니 기록을 고치세요."
                notes.append(f"{label}: 거래 기록으로 보유를 계산할 수 없음 — {g['error']}.{fallback}")
                continue
            if not decides(g) or pos.quantity <= 0:
                continue  # dividends only, or fully sold: the result is in the trade records' summary
            if g["ticker"] is None:
                notes.append(f"{label}: 이 거래 기록의 종목은 지금 쓰는 티커를 알 수 없음(티커가 다른 종목에 재사용됨 등) — 가격이 없어 평가에서 빠짐")
                continue
            sector, themes, rates = profile(g["ticker"])
            hs.append(Holding(g["ticker"], pos.quantity, pos.avg_cost, sector, themes, rates, source="ledger",
                              realized_pnl=pos.realized_pnl, dividends=pos.dividends))
        return Portfolio(tuple(hs), cash, tuple(notes))

    def ledger(self, s: Session, today: date | None = None) -> list[dict[str, Any]]:
        """Per security: its trade records, today's ticker, its splits and the holding computed by
        domain.ledger.positions (None with the reason when the records cannot be computed — never clamped)."""
        today = today or to_ny(self.now()).date()
        out = []
        for g in self.data.ledger_securities(s, repo.transactions(s)):
            trades = [ledger_trade(r) for r in g["rows"]]
            try:
                pos, err = positions(trades, g["splits"], today), None
            except LedgerError as e:
                pos, err = None, str(e)
            out.append(g | {"trades": trades, "position": pos, "error": err})
        return out

    def add_transaction(self, ticker: str, day: date, kind: str, quantity: float = 0.0, price: float = 0.0, fees: float = 0.0,
                        amount: float = 0.0, split_from: float = 0.0, split_to: float = 0.0, note: str = "") -> int:
        """Record one trade after checking it against the security's whole record (a back-dated trade may not make a
        later sale exceed the holding). Serialized: two submissions cannot both pass the check. Raises LedgerError."""
        with self._ledger_lock, self.sf() as s:
            today = to_ny(self.now()).date()
            sid = self.data.security_of(ticker, day, s)
            group = next((g for g in self.data.ledger_securities(s, repo.transactions(s)) if g["security"] == sid), None)
            trades = [ledger_trade(r) for r in group["rows"]] if group else []
            splits = group["splits"] if group else self.data.security_splits(sid, s)
            new = Trade(max((t.id for t in trades), default=0) + 1, day, kind, quantity, price, fees, amount, split_from, split_to)
            check_new(trades, new, splits, today)
            row = repo.add_transaction(s, ticker=ticker, day=day, kind=kind, quantity=quantity, price=price, fees=fees, amount=amount,
                                       split_from=split_from, split_to=split_to, note=note[:200])
            s.commit()
            return int(row.id)

    def delete_transaction(self, tid: int) -> None:
        """Delete one trade record unless that makes a sale exceed the holding. Raises LedgerError / LedgerNotFound."""
        with self._ledger_lock, self.sf() as s:
            today = to_ny(self.now()).date()
            group = next((g for g in self.data.ledger_securities(s, repo.transactions(s)) if any(r.id == tid for r in g["rows"])), None)
            if group is None:
                raise LedgerNotFound(f"거래 {tid}을(를) 찾을 수 없음")
            check_delete([ledger_trade(r) for r in group["rows"]], tid, group["splits"], today)
            repo.delete_transaction(s, tid)
            s.commit()

    def company_recommendations(self, s: Session, ticker: str, before: datetime | None = None, on: date | None = None, limit: int = 500,
                                inclusive: bool = True, exclude_id: int | None = None) -> list[RecommendationRow]:
        """See DataAccess.company_recommendations (the one implementation)."""
        return self.data.company_recommendations(s, ticker, self.mode.value, before, on or to_ny(before or self.now()).date(), limit, inclusive, exclude_id)

    def latest_company_recommendation(self, s: Session, ticker: str, before: datetime | None = None, inclusive: bool = True,
                                      exclude_id: int | None = None) -> RecommendationRow | None:
        rows = self.company_recommendations(s, ticker, before, limit=1, inclusive=inclusive, exclude_id=exclude_id)
        return rows[0] if rows else None

    def _previous_lookup(self, s: Session) -> Any:
        def lookup(ticker: str, as_of: datetime) -> tuple[AnalysisDigest | None, Action | None]:
            # recommendations already stored when this analysis runs (same timestamp included)
            # the company's latest recommendation — under an earlier ticker after a rename, never another company's
            # after a reuse (independent review F10; round 10 identity invariant)
            row = self.latest_company_recommendation(s, ticker, before=as_of)
            if row is None:
                return None, None
            digest = decode(AnalysisDigest, row.result["digest"])
            return digest, Action(row.deterministic_action)

        return lookup

    def scanner(self, cfg: ModelConfig, s: Session) -> Scanner:
        return Scanner(self.data, cfg, self.seed, self.theses, previous_lookup=self._previous_lookup(s))

    def provider_version(self) -> str:
        names = sorted({p.name for ch in self.registry.chains.values() for p in ch.providers})
        return f"{self.mode.value}:{__version__}:" + ",".join(names)[:100]

    def levels_now(self, row: RecommendationRow) -> dict[str, Any]:
        """A stored recommendation's price levels on today's share basis: a split executed after the analysis
        divides them by its ratio, as the paper trades and the next analysis do (8th evaluation I1) — the current
        quote is on the new basis."""
        entry = (row.result or {}).get("entry") or {}
        known = encoded_split_keys((row.inputs or {}).get("splits")) if isinstance(row.inputs, dict) else None
        f = share_multiplier(self.data.splits(row.ticker), analysis_basis(to_ny(row.as_of).date(), known), to_ny(self.now()).date()) or 1.0

        def adj(v: Any) -> float | None:
            return None if v is None else float(v) / f

        # every level of the plan, not only the headline ones (independent review 2026-09-28 F03: the stock page drew the
        # buy zone, second target and chart from the analysis snapshot — pre-split prices beside post-split ones)
        return {"split_factor": f, "price": adj(row.price)} | {k: adj(entry.get(k)) for k in PLAN_PRICE_FIELDS}

    def security_master(self, day: date) -> dict[str, Any]:
        """ticker → Security from the stored security master (LIVE) or the universe provider (MOCK), read at most every
        ten minutes — what holdings are labelled with before any market context exists (review 2026-09-28 F02)."""
        hit = self._master
        if hit is not None and hit[0] == day and time.monotonic() - hit[1] < 600:
            return hit[2]
        secs = {x.ticker: x for x in (self.data.securities(day).value or [])}
        self._master = (day, time.monotonic(), secs)
        return secs

    def _adopt_context(self, ctx: ScanContext) -> None:
        """The shared market context — issues screen, new-issue checks on stored recommendations, holdings' sectors — is
        the NEWEST one built, replaced whole after a scan or a single analysis finished (review 2026-09-28 F06). Company
        news the older one gathered for other names is carried over when still inside the news window, so a single
        analysis does not drop what the scan found; an older context never replaces a newer one."""
        with self._ctx_swap:
            old = self.last_scan_context
            if old is not None and ctx.as_of < old.as_of:
                return
            carried = getattr(old, "company_issues", None)
            if carried is not None and getattr(ctx, "issues", None) is not None:
                recent = carried.since(ctx.as_of - NEWS_LOOKBACK)
                ctx.issues = ctx.issues.merged(recent)
                ctx.company_issues = recent if ctx.company_issues is None else ctx.company_issues.merged(recent)
            self.last_scan_context = ctx

    def market_context(self) -> ScanContext:
        """The shared market context no older than ``CONTEXT_MAX_AGE`` (the issues screen): rebuilt once when older, one
        rebuild at a time — a second caller waits and uses it."""
        ctx = self.last_scan_context
        if ctx is not None and self.now() - ctx.as_of <= CONTEXT_MAX_AGE:
            return ctx
        with self._ctx_build:
            ctx = self.last_scan_context
            if ctx is None or self.now() - ctx.as_of > CONTEXT_MAX_AGE:
                with self.sf() as s:
                    self._adopt_context(self.scanner(self.model_config(), s).build_context(self.now()))
        return self.last_scan_context  # type: ignore[return-value]

    def recommendation_status(self, row: RecommendationRow, fetch_quote: bool = False) -> RecommendationFreshness:
        """Is this stored recommendation still current NOW (not just: was it fresh when it was made)?

        Same-session recommendations are re-checked against a current quote (max buy, stop, reward/risk,
        move since analysis). Listings only use an already cached quote; the stock page may fetch one."""
        lv = self.levels_now(row)
        bullish = row.final_action in {a.value for a in BULLISH_ACTIONS}
        plan = PlanCheck(lv["price"], lv["max_buy"], lv["stop"], lv["target1"], self.base_cfg.decision.min_rr, bullish)
        f = self.data.peek_quote(row.ticker)
        live = self.quotes.latest(row.ticker)  # the app-wide stream/snapshot state: no provider call
        if f is None and live is None and fetch_quote:
            f = self.data.quote(row.ticker)
        q: Any = f.value if f is not None else None
        if live is not None and (q is None or getattr(q, "timestamp", None) is None or live.trade_ts > q.timestamp):
            q = live
            q_price, q_ts = live.price, live.trade_ts
        else:
            q_price, q_ts = getattr(q, "price", None), getattr(q, "timestamp", None)
        majors: tuple[str, ...] = ()
        ctx = self.last_scan_context
        if ctx is not None and ctx.issues is not None:
            cut = self.base_cfg.major_issue_importance
            majors = tuple(i.title for i in ctx.issues.issues if i.importance >= cut and i.publish_time > row.as_of and row.ticker in i.affected_companies)
        return recommendation_freshness(row.as_of, row.data_quality, self.now(), plan=plan,
                                        quote_price=q_price, quote_ts=q_ts, new_major_events=majors, analysis_price_ts=row.price_timestamp)

    # ------------------------------------------------------------------ persistence
    def _persist(self, s: Session, r: AnalysisResult, inp: AnalysisInputs, cfg: ModelConfig, scan_id: int | None, rank: int | None, committee: CommitteeResult | None) -> RecommendationRow:
        ok = committee is not None and committee.status in COMMITTEE_OK
        final_action = committee.final_action if ok and committee else r.decision.action.value
        final_conf = committee.final_confidence if ok and committee else r.decision.confidence
        models = sorted({c.model for c in committee.calls if c.model not in ("?", "cache")}) if committee else []
        row = RecommendationRow(
            scan_run_id=scan_id, ticker=r.ticker, as_of=r.as_of, rank=rank, mode=r.mode.value, session=r.session,
            price=r.price, price_source=r.price_source, price_timestamp=r.price_timestamp, price_quality=r.price_quality.value,
            score=r.scorecard.total, confidence=final_conf, deterministic_action=r.decision.action.value, final_action=final_action,
            size_class=stored_size_class(r, committee),
            sector=r.security.sector, sector_model=r.sector_model_id, regime=r.primary_regime, data_quality=r.data_quality.overall.value,
            committee_status=committee.status if committee else "NOT_RUN", result=encode(r), inputs=encode(inp),
            model_config_snapshot=config_snapshot(CONFIG_DIR, dict(cfg.scoring_model.weights), cfg.scoring_model.version),
            input_fingerprint=r.input_fingerprint, scoring_model_version=cfg.scoring_model.version,
            decision_model_version=cfg.decision_model_version, agent_prompt_version=AGENT_PROMPT_VERSION,
            provider_version=self.provider_version(), config_version=f"{cfg.config_version}+{cfg.config_hash}",
            schema_version=SCHEMA_VERSION, code_version=code_version(), app_version=__version__,
            llm_model_ids=",".join(models)[:200] or None, created_at=repo.now(),
        )
        s.add(row)
        s.flush()
        if committee is not None:
            s.add(CommitteeRow(recommendation_id=row.id, status=committee.status, payload=committee.as_dict(), consensus=committee.consensus_pct, divergence=committee.divergence, prompt_version=committee.prompt_version, created_at=repo.now()))
            for c in committee.calls:
                repo.record_llm_call(s, c)
        s.add(FactorSnapshotRow(
            recommendation_id=row.id, ticker=r.ticker, rec_day=rec_session_day(r.as_of),
            factors={c.name: c.subscore for c in r.scorecard.components} | {"total": r.scorecard.total, "issue": (r.issue_score_swing / 100) if r.issue_score_swing is not None else None},
            total_score=r.scorecard.total, confidence=final_conf, action=final_action, sector=r.security.sector, regime=r.primary_regime,
            scoring_model_version=cfg.scoring_model.version,
        ))
        prev = self.latest_company_recommendation(s, r.ticker, before=r.as_of, exclude_id=row.id)
        if prev is not None and prev.final_action != final_action:
            log_event(log, Event.RECOMMENDATION_CHANGED, ticker=r.ticker, before=prev.final_action, after=final_action, reasons=[c.text for c in r.changes if c.material][:5])
        log_event(log, Event.DECISION_CREATED, ticker=r.ticker, action=final_action, score=r.scorecard.total)
        self._maybe_paper(s, row, r, cfg, final_action, final_conf)
        return row

    def _maybe_paper(self, s: Session, row: RecommendationRow, r: AnalysisResult, cfg: ModelConfig, final_action: str, final_conf: float) -> None:
        """One paper signal per actionable recommendation; repeated BUYs of an open position are not re-opened."""
        if not self.settings.enable_paper_trading or r.entry is None or r.mode != self.mode or Action(final_action) not in BULLISH_ACTIONS:
            return
        open_same = [p for p in repo.open_paper_positions(s) if p.ticker == r.ticker]
        if final_action in (Action.BUY.value, Action.BUY_SMALL.value) and open_same:
            log.info("paper: repeated %s for %s ignored (position already pending/open)", final_action, r.ticker)
            return
        if final_action == Action.ADD.value and not any(p.status == "OPEN" for p in open_same):
            log.info("paper: ADD for %s ignored (no open paper position)", r.ticker)
            return
        cap = recommendation_size_cap(row)
        if effective_size(final_action, cap) == "WATCH":  # the one size limit allows no new money (review 2026-09-28 F01/F05)
            log.info("paper: %s for %s ignored (size limit WATCH)", final_action, r.ticker)
            return
        s.add(PaperPositionRow(
            recommendation_id=row.id, ticker=r.ticker, recommended_at=r.as_of, status="PENDING", score=r.scorecard.total,
            confidence=final_conf, action=final_action, regime=r.primary_regime, sector=r.security.sector,
            stop=r.entry.stop, target1=r.entry.target1, target2=r.entry.target2, max_buy=r.entry.max_buy,
            notional=position_notional(final_action, cfg.paper, cap),
            thesis="; ".join(c.description for c in r.thesis_conditions[:3]), model_version=cfg.scoring_model.version, updated_at=repo.now(),
        ))

    def _external_news(self, r: AnalysisResult, ctx: ScanContext | None) -> list[tuple[str, str]]:
        """Only articles that reach this ticker (through an issue that impacts it, or direct relevance)."""
        arts = news_for_ticker(ctx.issues if ctx else None, r.ticker, [i.issue_id for i in r.issue_impacts])
        return [(a.source, f"{a.title}\n{a.summary}\n{a.body}") for a in arts]

    def run_committee(self, r: AnalysisResult, ctx: ScanContext | None, s: Session, depth: str = "FULL") -> CommitteeResult:
        cfg = self.base_cfg
        committee = Committee(self.llm, repo.DbLLMCache(s), cfg.committee_max_confidence_adjustment)
        return committee.run(r, self._external_news(r, ctx), set(ctx.securities) if ctx else None, depth=depth)

    def _reusable_committee(self, r: AnalysisResult, s: Session, max_sessions: int = 5) -> CommitteeResult | None:
        """Material-change gate: when nothing material changed since the last committee on this ticker (same
        deterministic action, no material change, ≤ ``max_sessions`` sessions old), carry its result forward
        instead of paying for the same debate again."""
        from marketlens.domain.market_calendar import last_completed_session, trading_days_between

        if any(c.material for c in r.changes):
            return None
        hit = repo.latest_committee_for_ticker(s, r.ticker)
        if hit is None:
            return None
        prev_rec, prev_com = hit
        if prev_com.status == "REUSED" and prev_com.payload.get("reused_from"):
            orig = repo.get_recommendation(s, int(prev_com.payload["reused_from"]))  # age counts from the real debate
            prev_rec = orig if orig is not None else prev_rec
        if prev_com.status not in COMMITTEE_OK or prev_rec.deterministic_action != r.decision.action.value or prev_rec.mode != self.mode.value:
            return None
        if trading_days_between(last_completed_session(prev_rec.as_of), last_completed_session(r.as_of)) > max_sessions:
            return None
        p = prev_com.payload
        return CommitteeResult(
            ticker=r.ticker, status="REUSED", reason=f"중요한 변화 없음 → {prev_rec.as_of.date().isoformat()} 위원회 결과 재사용(추가 AI 호출 없음)",
            deterministic_action=r.decision.action.value, final_action=p.get("final_action", r.decision.action.value),
            deterministic_confidence=r.decision.confidence, final_confidence=min(float(p.get("final_confidence", r.decision.confidence)), r.decision.confidence),
            size_class=p.get("size_class"), action_changed_by=p.get("action_changed_by"), synthesis=p.get("synthesis"), risk_review=p.get("risk_review"),
            portfolio_advice=p.get("portfolio_advice"), consensus_pct=p.get("consensus_pct"), divergence=p.get("divergence"),
            depth="REUSED", reused_from=prev_rec.id,
            reports=p.get("reports") or {}, debate=p.get("debate") or [],
        )

    # ------------------------------------------------------------------ use cases
    def run_scan(self, run_committee: bool = True, only: list[str] | None = None) -> ScanSummary:
        """One scan at a time: a second request while one runs is refused, not queued behind it (it would repeat the
        whole scan and its AI calls); a scan while the data preparation runs is refused too (it would judge
        half-prepared data). Round 10 concurrency invariant."""
        with self._jobs_guard:
            if self._sync_lock.locked() or self._sync_run.locked():
                raise ScanRefused("데이터를 준비하는 중에는 스캔할 수 없습니다 — 준비가 끝난 뒤 다시 시도하세요")
            if not self._lock.acquire(blocking=False):
                raise ScanRefused("이미 전체 시장 스캔이 진행 중입니다 — 끝나면 결과가 화면에 나옵니다")
        try:
            return self._run_scan_locked(run_committee, only)
        finally:
            self._lock.release()

    def _run_scan_locked(self, run_committee: bool, only: list[str] | None) -> ScanSummary:
        with self.sf() as s:
            cfg = self.model_config()
            as_of = self.now()
            pf = self.portfolio(s)
            sc = self.scanner(cfg, s)
            result: ScanResult = sc.run(as_of, pf, only=only)
            self._adopt_context(result.context)
            ctx = result.context
            scan = ScanRunRow(
                as_of=as_of, mode=self.mode.value, stages=[st.__dict__ for st in result.stages], excluded_count=len(result.excluded),
                regimes=[encode(x) for x in (result.candidates[0].regimes if result.candidates else ())],
                issues=[i.issue_id for i in (ctx.issues.issues if ctx.issues else [])],
                scoring_model_version=cfg.scoring_model.version, config_version=cfg.config_version, created_at=repo.now(),
            )
            s.add(scan)
            s.flush()
            total = len(result.candidates)
            self._scan_state(s, {"scan_id": scan.id, "status": "RUNNING", "started_at": as_of.isoformat(), "saved": 0, "total": total})
            s.commit()  # the scan row exists even if the process stops mid-way
            try:
                return self._scan_rest(s, result, cfg, scan, as_of, total, run_committee)
            except Exception as e:  # a failure half-way: the state says FAILED and what was saved (then the error propagates)
                s.rollback()
                with self.sf() as s2:
                    st = json.loads(repo.get_setting(s2, "scan_state", "") or "null") or {}
                    saved = int(st.get("saved", 0)) if st.get("scan_id") == scan.id else 0
                    self._scan_state(s2, {"scan_id": scan.id, "status": "FAILED", "started_at": as_of.isoformat(), "saved": saved, "total": total,
                                          "error": f"{type(e).__name__}: {str(e)[:200]}"})
                    s2.commit()
                raise

    def _scan_rest(self, s: Session, result: ScanResult, cfg: ModelConfig, scan: ScanRunRow, as_of: datetime, total: int, run_committee: bool) -> ScanSummary:
        ctx = result.context
        if self.store is None:  # the LIVE store is maintained by the sync job
            # full listing history incl. delisted names (the scan itself only sees the as-of universe)
            repo.upsert_securities(s, self.data.securities(None).value or ctx.securities.values(), self.mode.value)
            for r in result.candidates:
                inp = result.inputs[r.ticker]
                repo.store_fundamental_vintages(s, r.ticker, inp.quarters)
                repo.store_bars(s, r.ticker, inp.bars, inp.source_map.get("bars", "unknown"))
        if ctx.issues:
            repo.upsert_issues(s, [(i.issue_id, i.category.value, i.importance, encode(i) | {"injection_flagged": any(n in ctx.issues.injection_flags for n in i.news_ids)}) for i in ctx.issues.issues])
        committee_run = 0
        paper = 0
        top_n, full_n = cfg.scanner.ai_committee_top_n, cfg.scanner.committee_full_top_n
        for rank, r in enumerate(result.candidates, start=1):
            committee = None
            if run_committee and self.settings.enable_ai_committee and rank <= top_n:
                committee = self._reusable_committee(r, s) if r.decision.action != Action.DATA_INSUFFICIENT else None
                if committee is None:
                    committee = self.run_committee(r, ctx, s, depth="FULL" if rank <= full_n else "LIGHT")
                committee_run += committee.status not in ("SKIPPED", "REUSED")
            row = self._persist(s, r, result.inputs[r.ticker], cfg, scan.id, rank, committee)
            if Action(row.final_action) in BULLISH_ACTIONS and self.settings.enable_paper_trading:
                paper += 1
            # saved one by one: an interrupted scan keeps what it finished (and the AI committee results
            # already paid for are reused by the next scan) instead of losing the whole run
            self._scan_state(s, {"scan_id": scan.id, "status": "RUNNING", "started_at": as_of.isoformat(), "saved": rank, "total": total})
            s.commit()
        repo.snapshot_health(s, [h.as_dict() for h in self.health.all()])
        coverage = scan_coverage(result, repo.llm_usage(s, since=scan.created_at))
        repo.set_setting(s, "last_scan_coverage", json.dumps(coverage | {"scan_id": scan.id, "as_of": as_of.isoformat()}))
        self._scan_state(s, {"scan_id": scan.id, "status": "COMPLETE", "started_at": as_of.isoformat(), "saved": total, "total": total})
        s.commit()
        return ScanSummary(scan.id, as_of, len(result.candidates), committee_run, paper)

    @staticmethod
    def _scan_state(s: Session, state: dict[str, Any]) -> None:
        repo.set_setting(s, "scan_state", json.dumps(state))

    def scan_status(self) -> dict[str, Any]:
        """The last scan's progress and coverage. A scan still "RUNNING" while no scan holds the lock was
        interrupted (the app closed or crashed): its finished candidates are kept."""
        with self.sf() as s:
            state = json.loads(repo.get_setting(s, "scan_state", "") or "null") or {}
            cov = json.loads(repo.get_setting(s, "last_scan_coverage", "") or "null")
        if state.get("status") == "RUNNING" and not self._lock.locked():
            state["status"] = "INTERRUPTED"
        return {"state": state or None, "coverage": cov}

    def analyze(self, ticker: str, run_committee: bool = False, persist: bool = True) -> tuple[AnalysisResult, CommitteeResult | None, int | None]:
        ticker = ticker.upper()
        with self.sf() as s:
            cfg = self.model_config()
            sc = self.scanner(cfg, s)
            ctx = sc.build_context(self.now())
            r, inp = sc.analyze_single(ticker, ctx.as_of, self.portfolio(s), ctx)
            # the newer market context replaces the shared one once the analysis succeeded (review 2026-09-28 F06: it was
            # kept only when none existed, so the issues screen and new-issue checks stayed on a days-old context)
            self._adopt_context(ctx)
            committee = self.run_committee(r, ctx, s) if run_committee else None
            rec_id = None
            if persist:
                row = self._persist(s, r, inp, cfg, None, None, committee)
                s.commit()
                rec_id = row.id
            return r, committee, rec_id

    def committee_for_recommendation(self, rec_id: int) -> dict[str, Any]:
        """Run the AI committee on a stored recommendation snapshot.

        The issued recommendation is never edited. The committee result is stored as a NEW version that
        supersedes it (same analysis snapshot and ``as_of``, ``created_at`` = now); paper trading and
        outcome evaluation keep using the version as it was issued."""
        with self.sf() as s:
            base = repo.get_recommendation(s, rec_id)
            if base is None:
                raise KeyError(rec_id)
            cur = repo.current_version(s, rec_id) or base
            for r in (cur, base):
                existing = repo.committee_for(s, r.id)
                if existing is not None:
                    return existing.payload | {"recommendation_id": r.id}
            r = decode(AnalysisResult, base.result)
            c = self.run_committee(r, self.last_scan_context, s)
            for call in c.calls:
                repo.record_llm_call(s, call)
            ok = c.status in COMMITTEE_OK
            models = sorted({x.model for x in c.calls if x.model not in ("?", "cache")})
            cols = {k: getattr(cur, k) for k in RecommendationRow.__table__.columns.keys() if k not in ("id", "created_at", "supersedes_id", "version")}
            new = RecommendationRow(**cols, created_at=repo.now(), supersedes_id=cur.id, version=(cur.version or 1) + 1)
            new.committee_status = c.status
            if ok:
                new.final_action, new.confidence, new.size_class = c.final_action, c.final_confidence, stored_size_class(r, c)
                new.llm_model_ids = ",".join(models)[:200] or None
            s.add(new)
            s.flush()
            s.add(CommitteeRow(recommendation_id=new.id, status=c.status, payload=c.as_dict(), consensus=c.consensus_pct, divergence=c.divergence, prompt_version=c.prompt_version, created_at=repo.now()))
            s.commit()
            return c.as_dict() | {"recommendation_id": new.id, "supersedes_id": cur.id}

    def replay_recommendation(self, rec_id: int) -> ReplayOutcome:
        with self.sf() as s:
            row = repo.get_recommendation(s, rec_id)
            if row is None:
                raise KeyError(rec_id)
            return replay(row.inputs, row.model_config_snapshot, row.score, row.deterministic_action, row.input_fingerprint)

    def sync_market(self, **limits: Any) -> dict[str, Any]:
        """LIVE only: refresh the local point-in-time store from free sources (SEC / Polygon grouped daily).

        ``limits`` are passed to :meth:`MarketSync.run` (e.g. ``max_bar_calls``, ``max_profiles``)."""
        from marketlens.application.sync import MarketSync

        if self.store is None:
            return {"status": "SKIPPED", "reason": "MOCK 모드는 로컬 저장소 동기화가 필요 없음"}
        sc = self.base_cfg.scanner
        limits.setdefault("min_market_cap", sc.min_market_cap)
        limits.setdefault("min_dollar_volume", sc.min_avg_dollar_volume)
        limits.setdefault("min_price", sc.min_price)
        # another sync (the background job, POST /api/sync, the CLI, the scheduler) waits for the running one and then
        # computes its own missing days from the store: no session is downloaded twice. It never runs beside a scan:
        # after taking the sync lock it checks, under the same guard the scan claims with, that no scan holds the data
        self._sync_run.acquire()
        try:
            with self._jobs_guard:
                scanning = self._lock.locked()
            if scanning:
                return {"status": "REFUSED", "reason": "전체 시장 스캔 중에는 데이터를 준비하지 않습니다 — 스캔이 끝난 뒤 다시 시도하세요"}
            rep = MarketSync(self.registry, self.store).run(self.now(), **limits)
            rep.errors = [redact_text(e) for e in rep.errors]  # stored in settings and shown on screen
        finally:
            self._sync_run.release()
        self.data.cache = type(self.data.cache)()  # the store changed → drop cached provider reads
        # "sync finished" is not "data complete": a partial sync says how much is still missing
        out = {"status": "SYNC_COMPLETE" if rep.complete else "SYNC_PARTIAL", "bar_days_remaining": max(0, rep.bar_days_missing - rep.bar_days_loaded - rep.bar_days_empty), **rep.__dict__}
        self.store.set_setting("last_sync", json.dumps({"status": out["status"], "at": self.now().isoformat(), "bar_days_remaining": out["bar_days_remaining"], "errors": rep.errors[:5]}))
        return out

    def start_sync(self, max_rounds: int = 20) -> dict[str, Any]:
        """LIVE: prepare the local store in the background, one bounded :meth:`sync_market` round after another,
        until the price history window is complete and no fundamentals are waiting — or a round makes no
        progress. Every round keeps each provider's own rate limit; nothing is fetched twice."""
        if self.store is None:
            return {"started": False, "reason": "MOCK 모드는 데이터 준비가 필요 없음"}
        with self._jobs_guard:
            if self._lock.locked():
                return {"started": False, "reason": "전체 시장 스캔이 진행 중입니다 — 스캔이 끝난 뒤 데이터 준비를 시작하세요", **self.sync_status()}
            if not self._sync_lock.acquire(blocking=False):
                return {"started": False, "reason": "이미 데이터를 준비하는 중", **self.sync_status()}
        state: dict[str, Any] = {"status": "RUNNING", "round": 0, "started_at": self.now().isoformat()}
        self._sync_progress = {}
        try:
            self._sync_state(state)
            threading.Thread(target=self._sync_rounds, args=(max_rounds, state), name="marketlens-sync", daemon=True).start()
        except Exception:
            self._sync_lock.release()  # the job never started: the lock must not stay taken
            raise
        return {"started": True, **self.sync_status()}

    def _sync_state(self, state: dict[str, Any]) -> None:
        with self.sf() as s:
            repo.set_setting(s, "sync_job", json.dumps(state))
            s.commit()

    def _sync_rounds(self, max_rounds: int, state: dict[str, Any]) -> None:
        """Runs holding ``_sync_lock`` (taken by :meth:`start_sync`)."""
        try:
            for i in range(max_rounds):
                out = self.sync_market(progress=self._on_sync_progress)
                if out.get("status") == "REFUSED":  # cannot happen while the job holds the preparation lock; never loop on it
                    state.update(status="FAILED", errors=[out["reason"]], updated_at=self.now().isoformat())
                    break
                progressed = bool(out["bar_days_loaded"] or out["bar_days_empty"] or out["fundamentals_ingested"] or out["profiles_updated"]
                                  or out.get("shares_looked_up"))
                state.update(round=i + 1, bar_days_remaining=out["bar_days_remaining"], fundamentals_pending=out["fundamentals_pending"],
                             errors=out["errors"][:3], missing=out.get("missing", []), updated_at=self.now().isoformat())
                state["failures"] = list(dict.fromkeys(state.get("failures", []) + out.get("failures", [])))[:3]
                state["progress"] = self._progress_summary()
                # profiles have no pending count: a round that still added some may have left more (bounded per round)
                if out["status"] == "SYNC_COMPLETE" and not out["fundamentals_pending"] and not out["profiles_updated"] and not out.get("shares_pending"):
                    state["status"] = "DONE"
                    break
                if not progressed:  # the next round would repeat the same calls: stop and say why
                    # a required dataset without a key is never "done": the screen says what to set (owner report)
                    state["status"] = "NEEDS_SETUP" if state["missing"] else ("FAILED" if out["errors"] else "DONE")
                    break
                self._sync_state(state)
            else:
                state["status"] = "PAUSED"  # round cap reached; pressing the button again continues where it stopped
            state["progress"] = self._progress_summary()
            self._settle_sync(state)
        except Exception as e:  # the job must end with a visible state; the error is logged with its traceback
            log.exception("background sync failed")
            state.update(status="FAILED", errors=[f"{type(e).__name__}: {e}"])
        finally:
            self._sync_last = state  # kept in memory too: shown when the database refused the final write
            try:
                state["finished_at"] = self.now().isoformat()
                self._sync_state(state)
            except Exception:  # the final state could not be written (e.g. a busy database): logged, never keeps the lock
                log.exception("background sync: final state not saved")
            finally:
                self._sync_lock.release()  # always: the button must work again without restarting the app

    # seconds one item takes at the free providers' limits (Polygon 5 requests/minute; SEC filings are large downloads):
    # used for the weights of the overall percentage and for the time estimate until a measured pace exists
    SYNC_SECONDS_PER_ITEM = {"bars": 12.5, "universe": 10.0, "profiles": 0.5, "fundamentals": 1.5}

    def _on_sync_progress(self, step: str, done: int, total: int, detail: str = "") -> None:
        """MarketSync's progress callback: counts of the whole preparation after every stored item."""
        import time

        now = time.monotonic()
        rec = self._sync_progress.get(step)
        if rec is None or done < rec["first"]:
            rec = {"first": done, "t0": now}
            self._sync_progress[step] = rec
        rec.update(done=done, total=total, detail=detail, t=now)
        self._sync_progress["_current"] = step

    def _progress_summary(self) -> dict[str, Any] | None:
        """{steps: {step: done/total/percent/detail}, percent, eta_seconds, current}: the overall percentage weighs
        each step's items by the time one takes; 100 only when every item of every reported step is done. The time
        left uses the pace measured in this job once three items went through, else the providers' limits."""
        import math

        steps = {k: v for k, v in self._sync_progress.items() if not k.startswith("_")}
        if not steps:
            return None
        out: dict[str, Any] = {}
        work = done_work = eta = 0.0
        for k, v in steps.items():
            total, done = int(v["total"]), min(int(v["done"]), int(v["total"]))
            spu = self.SYNC_SECONDS_PER_ITEM.get(k, 1.0)
            n, dt = done - v["first"], v["t"] - v["t0"]
            pace = dt / n if n >= 3 and dt > 0 else spu
            out[k] = {"done": done, "total": total, "percent": math.floor(100 * done / total) if total else 100, "detail": v.get("detail", "")}
            work += total * spu
            done_work += done * spu
            eta += (total - done) * pace
        percent = 100.0 if done_work >= work else math.floor(1000 * done_work / work) / 10
        return {"steps": out, "percent": percent, "eta_seconds": round(eta), "current": self._sync_progress.get("_current")}

    def _settle_sync(self, state: dict[str, Any]) -> None:
        """The job's last word agrees with the recommendation readiness (owner report: "done" in 10 s while the
        screen stayed NOT READY). A missing key → NEEDS_SETUP; nothing left to fetch while still NOT READY →
        INCOMPLETE with the readiness reasons, the failures seen and when a retry is allowed; DONE only otherwise."""
        from marketlens.application.data_access import FUNDAMENTALS
        from marketlens.application.readiness import missing_setup

        missing = missing_setup(self.registry)
        state["missing"] = missing
        rd = self.readiness()
        if rd["recommendation_readiness"] != "NOT READY":
            return
        generic = "스캐너 데이터 준비가 끝나지 않아"
        reasons = [r for r in list(rd["scanner_reasons"]) + list(rd["readiness_reasons"]) if r not in missing and not r.startswith(generic)]
        state["reasons"] = list(dict.fromkeys(reasons))[:8]
        now = self.now()
        # only a failure that waiting can fix has a retry time; a tag the parser cannot read (PARSE_GAP) or a filer
        # without quarterly us-gaap facts is not fixed by pressing again later (owner report: "retry at 10-04")
        waits = [m.next_attempt_at for m in self.store.ingestion_all(FUNDAMENTALS).values()
                 if m.status in ("FAILED", "RATE_LIMITED") and m.next_attempt_at is not None and m.next_attempt_at > now] if self.store is not None else []
        if waits:
            state["retry_at"] = min(waits).isoformat()
        if missing and state["status"] in ("DONE", "PAUSED", "FAILED"):
            state["status"] = "NEEDS_SETUP"  # what it could fetch it did (errors stay listed); readiness needs the key first
        elif state["status"] == "DONE":
            state["status"] = "INCOMPLETE"

    def sync_status(self) -> dict[str, Any]:
        """The background data preparation's progress. RUNNING while no job holds the lock = the app was closed mid-way."""
        if self.store is None:
            return {"job": None}
        with self.sf() as s:
            job = json.loads(repo.get_setting(s, "sync_job", "") or "null")
        if job and job.get("status") == "RUNNING" and not self._sync_lock.locked():
            last = getattr(self, "_sync_last", None)
            if last is not None and last.get("started_at") == job.get("started_at"):
                return {"job": {**last, "note": "마지막 상태를 저장하지 못해 메모리의 결과를 보여 줌"}}
            job["status"] = "INTERRUPTED"
        if job and job.get("status") == "RUNNING":
            live = self._progress_summary()  # item by item, not only at the end of a round
            if live is not None:
                job["progress"] = live
        return {"job": job}

    def readiness(self) -> dict[str, Any]:
        """Scanner readiness + recommendation readiness gate + per-category data status (see readiness.py)."""
        from marketlens.application.readiness import evaluate
        from marketlens.domain.market_calendar import last_completed_session

        sc = self.base_cfg.scanner
        today = last_completed_session(self.now())
        sync_state = self.store.get_setting("last_sync") if self.store is not None else None
        verified_s = self.store.get_setting("live_verified") if self.store is not None else None
        # the coverage counts read the whole market (seconds on a full store): computed again only when the stored data
        # changed (owner report 2026-09-28: every dashboard / candidates screen waited about 5 s for them)
        key = (today, sync_state, verified_s, self.store.data_fingerprint() if self.store is not None else None)
        cached = getattr(self, "_readiness_cache", None)
        if cached is not None and cached[0] == key:
            stats = cached[1]
        else:
            stats = self.store.coverage_stats(today, sc.min_market_cap, sc.min_avg_dollar_volume, sc.min_price) if self.store is not None else None
            self._readiness_cache = (key, stats)
        return evaluate(self.mode.value, self.registry, stats, sync_state, json.loads(verified_s or "{}")).as_dict()
