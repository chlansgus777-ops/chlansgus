"""MarketLens application service: the single entry point used by the API, CLI and workers."""

from __future__ import annotations

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
from marketlens.domain.freshness import RecommendationFreshness, recommendation_freshness
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
COMMITTEE_OK = ("COMPLETED", "PARTIAL")


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

    def recommendation_status(self, row: RecommendationRow) -> RecommendationFreshness:
        """Is this stored recommendation still current NOW (not just: was it fresh when it was made)?"""
        return recommendation_freshness(row.as_of, row.data_quality, self.now())

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

    def run_committee(self, r: AnalysisResult, ctx: ScanContext | None, s: Session) -> CommitteeResult:
        cfg = self.base_cfg
        committee = Committee(self.llm, repo.DbLLMCache(s), cfg.committee_max_confidence_adjustment)
        return committee.run(r, self._external_news(r, ctx), set(ctx.securities) if ctx else None)

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
            top_n = cfg.scanner.ai_committee_top_n
            for rank, r in enumerate(result.candidates, start=1):
                committee = None
                if run_committee and self.settings.enable_ai_committee and rank <= top_n:
                    committee = self.run_committee(r, ctx, s)
                    committee_run += committee.status != "SKIPPED"
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
        with self.sf() as s:
            row = repo.get_recommendation(s, rec_id)
            if row is None:
                raise KeyError(rec_id)
            existing = repo.committee_for(s, rec_id)
            if existing is not None:
                return existing.payload
            r = decode(AnalysisResult, row.result)
            c = self.run_committee(r, self.last_scan_context, s)
            s.add(CommitteeRow(recommendation_id=row.id, status=c.status, payload=c.as_dict(), consensus=c.consensus_pct, divergence=c.divergence, prompt_version=c.prompt_version, created_at=repo.now()))
            for call in c.calls:
                repo.record_llm_call(s, call)
            row.committee_status = c.status
            if c.status in COMMITTEE_OK:
                row.final_action, row.confidence, row.size_class = c.final_action, c.final_confidence, c.size_class
                models = sorted({x.model for x in c.calls if x.model not in ("?", "cache")})
                row.llm_model_ids = ",".join(models)[:200] or None
                snap = s.get(FactorSnapshotRow, row.id)
                if snap is not None:
                    snap.action, snap.confidence = c.final_action, c.final_confidence
                self._sync_paper_with_final(s, row)
            s.commit()
            return c.as_dict()

    def _sync_paper_with_final(self, s: Session, row: RecommendationRow) -> None:
        """A committee downgrade after persistence must be reflected in the paper signal."""
        pos = next((p for p in repo.all_paper_positions(s) if p.recommendation_id == row.id), None)
        if pos is None:
            return
        if Action(row.final_action) not in BULLISH_ACTIONS:
            if pos.status == "PENDING":
                pos.status, pos.skip_reason = "SKIPPED", f"AI 위원회 하향 조정({row.deterministic_action} → {row.final_action})"
        elif pos.status == "PENDING" and pos.action != row.final_action:
            pos.action, pos.notional = row.final_action, position_notional(row.final_action, self.base_cfg.paper)
        pos.updated_at = repo.now()

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
        return {"status": "OK" if not rep.errors else "PARTIAL", **rep.__dict__}
