"""MarketLens application service: the single entry point used by the API, CLI and workers."""

from __future__ import annotations

import json

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

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
from marketlens.application.scanner import ScanContext, ScanResult, Scanner
from marketlens.application.theses import ThesisBook
from marketlens.config import AGENT_PROMPT_VERSION, CONFIG_DIR, SCHEMA_VERSION, ModelConfig, Settings, code_version, load_model_config
from marketlens.domain.enums import BULLISH_ACTIONS, Action, DataMode
from marketlens.domain.freshness import PlanCheck, RecommendationFreshness, recommendation_freshness
from marketlens.domain.market_calendar import UTC
from marketlens.domain.paper import position_notional
from marketlens.domain.portfolio import Holding, Portfolio
from marketlens.domain.what_changed import AnalysisDigest
from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import CommitteeRow, FactorSnapshotRow, PaperPositionRow, RecommendationRow, ScanRunRow
from marketlens.infrastructure.health import HealthRegistry
from marketlens.infrastructure.logging import Event, log_event
from marketlens.providers.llm.base import LLMProvider, UnavailableLLM

log = logging.getLogger("marketlens.service")
DEFAULT_CASH = 100_000.0
COMMITTEE_OK = ("COMPLETED", "PARTIAL", "REUSED")


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
        self.data = DataAccess(self.registry, self.base_cfg.cache_ttl, store=self.store)
        self._lock = threading.Lock()
        self.last_scan_context: ScanContext | None = None
        self._check_db_environment()

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
        rows = repo.holdings(s)
        cash = float(repo.get_setting(s, "portfolio_cash", str(DEFAULT_CASH)) or DEFAULT_CASH)
        hs = []
        for r in rows:
            sec = (self.last_scan_context.securities.get(r.ticker) if self.last_scan_context else None)
            exp = self.seed.macro_exposure(sec) if sec else None
            hs.append(Holding(r.ticker, r.quantity, r.cost_basis, sec.sector if sec else "Unknown", ("AI",) if exp and exp.ai >= 0.4 else (), exp.rates if exp else 0.0))
        return Portfolio(tuple(hs), cash)

    def _previous_lookup(self, s: Session) -> Any:
        def lookup(ticker: str, as_of: datetime) -> tuple[AnalysisDigest | None, Action | None]:
            # recommendations already stored when this analysis runs (same timestamp included)
            row = repo.latest_recommendation(s, ticker, before=as_of, mode=self.mode.value, inclusive=True)
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

    def recommendation_status(self, row: RecommendationRow, fetch_quote: bool = False) -> RecommendationFreshness:
        """Is this stored recommendation still current NOW (not just: was it fresh when it was made)?

        Same-session recommendations are re-checked against a current quote (max buy, stop, reward/risk,
        move since analysis). Listings only use an already cached quote; the stock page may fetch one."""
        entry = (row.result or {}).get("entry") or {}
        bullish = row.final_action in {a.value for a in BULLISH_ACTIONS}
        plan = PlanCheck(row.price, entry.get("max_buy"), entry.get("stop"), entry.get("target1"), self.base_cfg.decision.min_rr, bullish)
        f = self.data.peek_quote(row.ticker)
        if f is None and fetch_quote:
            f = self.data.quote(row.ticker)
        q = f.value if f is not None else None
        majors: tuple[str, ...] = ()
        ctx = self.last_scan_context
        if ctx is not None and ctx.issues is not None:
            cut = self.base_cfg.major_issue_importance
            majors = tuple(i.title for i in ctx.issues.issues if i.importance >= cut and i.publish_time > row.as_of and row.ticker in i.affected_companies)
        return recommendation_freshness(row.as_of, row.data_quality, self.now(), plan=plan,
                                        quote_price=getattr(q, "price", None), quote_ts=getattr(q, "timestamp", None), new_major_events=majors)

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
            size_class=(committee.size_class if committee else (r.portfolio_review.size_cap.value if r.portfolio_review else None)),
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
        prev = repo.latest_recommendation(s, r.ticker, before=r.as_of, mode=r.mode.value, inclusive=True, exclude_id=row.id)
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
        s.add(PaperPositionRow(
            recommendation_id=row.id, ticker=r.ticker, recommended_at=r.as_of, status="PENDING", score=r.scorecard.total,
            confidence=final_conf, action=final_action, regime=r.primary_regime, sector=r.security.sector,
            stop=r.entry.stop, target1=r.entry.target1, target2=r.entry.target2, max_buy=r.entry.max_buy,
            notional=position_notional(final_action, cfg.paper),
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
        with self._lock, self.sf() as s:
            cfg = self.model_config()
            as_of = self.now()
            pf = self.portfolio(s)
            sc = self.scanner(cfg, s)
            result: ScanResult = sc.run(as_of, pf, only=only)
            self.last_scan_context = result.context
            ctx = result.context
            scan = ScanRunRow(
                as_of=as_of, mode=self.mode.value, stages=[st.__dict__ for st in result.stages], excluded_count=len(result.excluded),
                regimes=[encode(x) for x in (result.candidates[0].regimes if result.candidates else ())],
                issues=[i.issue_id for i in (ctx.issues.issues if ctx.issues else [])],
                scoring_model_version=cfg.scoring_model.version, config_version=cfg.config_version, created_at=repo.now(),
            )
            s.add(scan)
            s.flush()
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
            repo.snapshot_health(s, [h.as_dict() for h in self.health.all()])
            s.commit()
            return ScanSummary(scan.id, as_of, len(result.candidates), committee_run, paper)

    def analyze(self, ticker: str, run_committee: bool = False, persist: bool = True) -> tuple[AnalysisResult, CommitteeResult | None, int | None]:
        ticker = ticker.upper()
        with self.sf() as s:
            cfg = self.model_config()
            sc = self.scanner(cfg, s)
            ctx = sc.build_context(self.now())
            self.last_scan_context = self.last_scan_context or ctx
            r, inp = sc.analyze_single(ticker, ctx.as_of, self.portfolio(s), ctx)
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
                new.final_action, new.confidence, new.size_class = c.final_action, c.final_confidence, c.size_class
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
        rep = MarketSync(self.registry, self.store).run(self.now(), **limits)
        self.data.cache = type(self.data.cache)()  # the store changed → drop cached provider reads
        # "sync finished" is not "data complete": a partial sync says how much is still missing
        out = {"status": "SYNC_COMPLETE" if rep.complete else "SYNC_PARTIAL", "bar_days_remaining": max(0, rep.bar_days_missing - rep.bar_days_loaded - rep.bar_days_empty), **rep.__dict__}
        self.store.set_setting("last_sync", json.dumps({"status": out["status"], "at": self.now().isoformat(), "bar_days_remaining": out["bar_days_remaining"], "errors": rep.errors[:5]}))
        return out

    def readiness(self) -> dict[str, Any]:
        """Scanner readiness + recommendation readiness gate + per-category data status (see readiness.py)."""
        from marketlens.application.readiness import evaluate
        from marketlens.domain.market_calendar import last_completed_session

        stats = self.store.coverage_stats(last_completed_session(self.now()), self.base_cfg.scanner.min_market_cap) if self.store is not None else None
        sync_state = self.store.get_setting("last_sync") if self.store is not None else None
        verified = json.loads(self.store.get_setting("live_verified") or "{}") if self.store is not None else {}
        return evaluate(self.mode.value, self.registry, stats, sync_state, verified).as_dict()
