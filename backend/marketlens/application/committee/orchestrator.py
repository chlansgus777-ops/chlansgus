"""Committee orchestration: 7 analysts → Bull/Bear (2 rounds) → Decision Synthesizer → Risk Manager →
Portfolio Manager → deterministic consensus → downgrade-only application.

This module never touches the database. Caching and cost recording are injected callbacks.
"""

from __future__ import annotations

import hashlib
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Protocol, TypeVar

from pydantic import BaseModel

from marketlens.application.committee.consensus import DIVERGENCE_CONFIDENCE_PENALTY, consensus
from marketlens.application.committee.guard import parse_strict, sanitize
from marketlens.application.committee.prompts import PROMPT_VERSION, build_prompt, evidence_pack, pack_evidence
from marketlens.application.committee.schemas import ANALYSTS, AgentReport, DebateArgument, PortfolioAdvice, RiskReview, Synthesis
from marketlens.application.evidence import Evidence
from marketlens.application.pipeline import AnalysisResult
from marketlens.config import SCHEMA_VERSION
from marketlens.domain.decision import apply_downgrade, bounded_confidence
from marketlens.domain.enums import Action
from marketlens.domain.market_calendar import UTC
from marketlens.infrastructure.logging import Event, log_event
from marketlens.providers.llm.base import LLMError, LLMProvider, LLMUnavailable, estimate_cost, strict_schema

log = logging.getLogger("marketlens.committee")
M = TypeVar("M", bound=BaseModel)

ANALYST_COMPONENT = {
    "fundamental": "fundamental",
    "earnings": "earnings_revision",
    "valuation": "valuation",
    "macro": "macro",
    "technical": "technical",
    "news": "catalyst",
    "risk_analyst": "risk",
}
TIER = {"bull": "deep", "bear": "deep", "synthesizer": "deep", "risk_manager": "deep", "portfolio_manager": "fast"}


@dataclass
class LLMCallRecord:
    provider: str
    model: str
    tier: str
    purpose: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    estimated_cost_usd: float | None  # None = model price unknown (never reported as $0)
    cached: bool
    error: str | None
    created_at: datetime


class LLMCache(Protocol):
    def get(self, fingerprint: str) -> str | None: ...

    def put(self, fingerprint: str, model: str, response: str) -> None: ...


class MemoryLLMCache:
    def __init__(self) -> None:
        self._d: dict[str, str] = {}

    def get(self, fingerprint: str) -> str | None:
        return self._d.get(fingerprint)

    def put(self, fingerprint: str, model: str, response: str) -> None:
        self._d[fingerprint] = response


@dataclass
class CommitteeResult:
    ticker: str
    status: str  # COMPLETED | PARTIAL | UNAVAILABLE | SKIPPED | REUSED
    reason: str | None
    deterministic_action: str
    final_action: str
    deterministic_confidence: float
    final_confidence: float
    size_class: str | None
    action_changed_by: str | None
    reports: dict[str, dict[str, Any]] = field(default_factory=dict)
    invalid_outputs: dict[str, str] = field(default_factory=dict)
    debate: list[dict[str, Any]] = field(default_factory=list)
    synthesis: dict[str, Any] | None = None
    risk_review: dict[str, Any] | None = None
    portfolio_advice: dict[str, Any] | None = None
    consensus_pct: float | None = None
    divergence: str | None = None
    guard: dict[str, dict[str, Any]] = field(default_factory=dict)
    injection_flags: list[str] = field(default_factory=list)
    calls: list[LLMCallRecord] = field(default_factory=list)
    prompt_version: str = PROMPT_VERSION
    depth: str = "FULL"  # FULL (7 analysts, 2 debate rounds, synthesizer, risk, portfolio) | LIGHT | REUSED
    reused_from: int | None = None  # recommendation id whose committee result was carried forward

    def as_dict(self) -> dict[str, Any]:
        d = dict(self.__dict__)
        d["calls"] = [c.__dict__ | {"created_at": c.created_at.isoformat()} for c in self.calls]
        return d


class Committee:
    def __init__(self, llm: LLMProvider, cache: LLMCache | None = None, max_confidence_adjustment: float = 10.0, recorder: Callable[[LLMCallRecord], None] | None = None, parallel: int = 4) -> None:
        self.llm = llm
        self.cache = cache or MemoryLLMCache()
        self.max_adj = max_confidence_adjustment
        self.recorder = recorder
        self.parallel = parallel
        self._known: set[str] = set()
        self._universe: tuple[Evidence, ...] = ()
        self._ticker = ""

    def cache_key(self, tier: str, system: str, user: str, schema: dict[str, Any]) -> str:
        """Provider, exact model ID and generation settings, prompt/schema versions and the full prompt.

        Changing the model (or its settings) can never return a response cached for another model."""
        sig_fn = getattr(self.llm, "signature", None)
        sig = sig_fn(tier) if callable(sig_fn) else f"{getattr(self.llm, 'name', '')}|unknown-model"
        return hashlib.sha256(json.dumps([PROMPT_VERSION, SCHEMA_VERSION, tier, getattr(self.llm, "name", ""), sig, system, user, schema], sort_keys=True).encode()).hexdigest()

    # ------------------------------------------------------------------ LLM call with cache/guard
    def _ask(self, model_cls: type[M], role: str, tier: str, system: str, user: str, pack: list[Evidence], res: CommitteeResult) -> M | None:
        schema = strict_schema(model_cls.model_json_schema())
        fp = self.cache_key(tier, system, user, schema)
        cached = self.cache.get(fp)
        text: str | None = cached
        rec: LLMCallRecord
        if text is None:
            try:
                resp = self.llm.complete_json(system, user, schema, tier)
            except LLMUnavailable:
                raise
            except LLMError as e:
                rec = LLMCallRecord(self.llm.name, "?", tier, role, 0, 0, 0.0, 0.0, False, str(e), datetime.now(tz=UTC))
                res.calls.append(rec)
                if self.recorder:
                    self.recorder(rec)
                res.invalid_outputs[role] = f"LLM error: {e}"
                return None
            text = resp.text
            rec = LLMCallRecord(self.llm.name, resp.model, tier, role, resp.input_tokens, resp.output_tokens, resp.latency_ms, estimate_cost(resp.model, resp.input_tokens, resp.output_tokens), False, None, datetime.now(tz=UTC))
        else:
            rec = LLMCallRecord(self.llm.name, "cache", tier, role, 0, 0, 0.0, 0.0, True, None, datetime.now(tz=UTC))
        res.calls.append(rec)
        if self.recorder:
            self.recorder(rec)
        obj, err = parse_strict(model_cls, text)
        if obj is None:
            res.invalid_outputs[role] = err or "invalid output"
            log_event(log, Event.AGENT_OUTPUT_REJECTED, level=logging.WARNING, role=role, reason=err)
            return None
        if cached is None:
            self.cache.put(fp, rec.model, text)
        clean, rep = sanitize(obj, pack, self._ticker, self._known, self._universe)
        if rep.rejected_claims or rep.invalid_evidence_ids:
            res.guard[role] = {"rejected_claims": rep.rejected_claims, "invalid_evidence_ids": rep.invalid_evidence_ids, "confidence_scale": rep.confidence_scale}
            log_event(log, Event.AGENT_OUTPUT_REJECTED, level=logging.WARNING, role=role, rejected=len(rep.rejected_claims), invalid_ids=len(rep.invalid_evidence_ids))
        if rep.reject_output:
            res.invalid_outputs[role] = "근거 없는 출력: 인용한 근거 ID가 모두 무효이거나 유효한 논점이 없음"
            return None
        return clean

    # ------------------------------------------------------------------ main
    def run(self, r: AnalysisResult, external_news: list[tuple[str, str]] | None = None, known_tickers: set[str] | None = None, depth: str = "FULL") -> CommitteeResult:
        """``depth``: FULL = 14 calls (7 analysts, bull/bear ×2, synthesizer, risk, portfolio);
        LIGHT = 5 calls (fundamental, valuation, news analysts + risk manager + portfolio manager) — the two
        roles that may downgrade are always present."""
        det = r.decision
        res = CommitteeResult(
            ticker=r.ticker, status="COMPLETED", reason=None, deterministic_action=det.action.value, final_action=det.action.value,
            deterministic_confidence=det.confidence, final_confidence=det.confidence,
            size_class=r.portfolio_review.size_cap.value if r.portfolio_review else None, action_changed_by=None, depth=depth,
        )
        self._depth = depth
        if det.action == Action.DATA_INSUFFICIENT:
            # downgrade-only: nothing the committee says can change DATA INSUFFICIENT → don't spend on it
            res.status, res.reason = "SKIPPED", "데이터 부족/오래됨(하드 거부권) → AI 위원회 생략(결과를 바꿀 수 없음)"
            return res
        if not getattr(self.llm, "available", False):
            res.status, res.reason = "UNAVAILABLE", f"AI COMMITTEE UNAVAILABLE: {getattr(self.llm, 'reason', 'LLM 제공자 미설정')}"
            log_event(log, Event.COMMITTEE_UNAVAILABLE, ticker=r.ticker)
            return res
        log_event(log, Event.COMMITTEE_STARTED, ticker=r.ticker)
        self._ticker = r.ticker
        self._universe = r.evidence
        self._known = {t for t in (known_tickers or set()) if len(t) >= 2} | {r.ticker}
        valid_ids = {e.evidence_id for e in r.evidence}
        from marketlens.application.safety import detect_injection

        for src, txt in external_news or []:
            if detect_injection(txt):
                res.injection_flags.append(src)
        try:
            self._run_inner(r, res, valid_ids, external_news or [])
        except LLMUnavailable as e:
            res.status, res.reason = "UNAVAILABLE", f"AI COMMITTEE UNAVAILABLE: {e}"
            res.final_action, res.final_confidence = det.action.value, det.confidence
            log_event(log, Event.COMMITTEE_UNAVAILABLE, ticker=r.ticker)
            return res
        if res.invalid_outputs:
            res.status = "PARTIAL"
        log_event(log, Event.COMMITTEE_FINISHED, ticker=r.ticker, status=res.status, final_action=res.final_action)
        return res

    def _run_inner(self, r: AnalysisResult, res: CommitteeResult, valid_ids: set[str], external: list[tuple[str, str]]) -> None:
        det = r.decision
        sm = r.sector_model_id

        def analyst(name: str) -> tuple[str, AgentReport | None]:
            pack = evidence_pack(r.evidence, name)
            comp = r.scorecard.component(ANALYST_COMPONENT[name])
            ctx = {"component_subscore": comp.subscore if comp.available else None, "component_available": comp.available}
            system, user = build_prompt(name, r.ticker, sm, pack, ctx, external if name == "news" else None)
            rep = self._ask(AgentReport, name, "fast", system, user, pack_evidence(r.evidence, name), res)
            if rep is not None and rep.agent != name:
                res.invalid_outputs[name] = "agent field does not match role"
                return name, None
            return name, rep

        roles = ANALYSTS if getattr(self, "_depth", "FULL") == "FULL" else LIGHT_ANALYSTS
        with ThreadPoolExecutor(max_workers=self.parallel) as ex:
            results = list(ex.map(analyst, roles))
        reports = {n: rep for n, rep in results if rep is not None}
        res.reports = {n: rep.model_dump() for n, rep in reports.items()}
        cons, div = consensus(reports, sm)
        res.consensus_pct, res.divergence = cons, div

        # ---- Bull / Bear: 2 fixed rounds, evidence-cited
        summaries = {n: {"stance": rep.stance, "confidence": rep.confidence, "evidence_ids": rep.evidence_ids} for n, rep in reports.items()}
        prev: dict[str, Any] = {}
        light = getattr(self, "_depth", "FULL") != "FULL"
        for rnd in (() if light else (1, 2)):
            for side in ("bull", "bear"):
                pack = evidence_pack(r.evidence, side)
                ctx = {"round": rnd, "agent_reports": summaries, "opponent_previous": prev.get("bear" if side == "bull" else "bull")}
                system, user = build_prompt(side, r.ticker, sm, pack, ctx)
                arg = self._ask(DebateArgument, f"{side}_r{rnd}", TIER[side], system, user, pack_evidence(r.evidence, side), res)
                if arg is not None and (arg.side != side or arg.round != rnd):
                    res.invalid_outputs[f"{side}_r{rnd}"] = "side/round mismatch"
                    arg = None
                if arg is not None:
                    res.debate.append(arg.model_dump())
                    prev[side] = arg.model_dump()

        # ---- Synthesizer
        pack = evidence_pack(r.evidence, "synthesizer")
        ctx_s = {"consensus_pct": cons, "divergence": div, "deterministic_score": r.scorecard.total, "deterministic_action": det.action.value, "debate": res.debate}
        system, user = build_prompt("synthesizer", r.ticker, sm, pack, ctx_s)
        extra = [Evidence("COMMITTEE_CONSENSUS", "score", "위원회 합의도", cons, "calc", None, "FRESH", "committee.consensus", r.ticker, "pct")] if cons is not None else []
        syn = None if light else self._ask(Synthesis, "synthesizer", TIER["synthesizer"], system, user, pack_evidence(r.evidence, "synthesizer") + extra, res)
        res.synthesis = syn.model_dump() if syn else None

        # ---- Risk manager (downgrade-only)
        pack = evidence_pack(r.evidence, "risk_manager")
        ctx_r = {"deterministic_action": det.action.value, "vetoes": [v.value for v in det.vetoes], "event_risk": r.event_risk.level, "synthesis": res.synthesis}
        system, user = build_prompt("risk_manager", r.ticker, sm, pack, ctx_r)
        risk = self._ask(RiskReview, "risk_manager", TIER["risk_manager"], system, user, pack_evidence(r.evidence, "risk_manager"), res)
        res.risk_review = risk.model_dump() if risk else None

        # ---- Portfolio manager (size ≤ deterministic cap)
        pr = r.portfolio_review
        pack = evidence_pack(r.evidence, "portfolio_manager")
        ctx_p = {"size_cap": pr.size_cap.value if pr else "HALF", "fit": pr.fit if pr else "NEUTRAL", "warnings": list(pr.warnings) if pr else []}
        system, user = build_prompt("portfolio_manager", r.ticker, sm, pack, ctx_p)
        pm = self._ask(PortfolioAdvice, "portfolio_manager", TIER["portfolio_manager"], system, user, pack_evidence(r.evidence, "portfolio_manager"), res)
        res.portfolio_advice = pm.model_dump() if pm else None

        final, changed_by = apply_committee(det.action, risk, pm, pr.size_cap.value if pr else None)
        res.final_action = final.value
        res.action_changed_by = changed_by
        if changed_by:
            log_event(log, Event.RISK_VETO, ticker=r.ticker, from_action=det.action.value, to_action=final.value, by=changed_by)
        adj = (syn.confidence_adjustment if syn else 0.0) + DIVERGENCE_CONFIDENCE_PENALTY.get(div or "LOW", 0.0)
        res.final_confidence = bounded_confidence(det.confidence, adj, self.max_adj)
        sizes = ["WATCH", "SMALL", "HALF", "FULL"]
        cap = pr.size_cap.value if pr else "FULL"
        advised = pm.suggested_size if pm else cap
        res.size_class = sizes[min(sizes.index(cap), sizes.index(advised))]


SIZE_TO_ACTION = {"SMALL": Action.BUY_SMALL, "WATCH": Action.WATCH}
LIGHT_ANALYSTS = ("fundamental", "valuation", "news")


def apply_committee(deterministic: Action, risk: RiskReview | None, pm: PortfolioAdvice | None, size_cap: str | None) -> tuple[Action, str | None]:
    """The committee can only downgrade. Hard vetoes live in the deterministic action and can't be undone."""
    action = deterministic
    changed_by = None
    if risk is not None and risk.recommended_action is not None:
        new, ok = apply_downgrade(action, Action(risk.recommended_action))
        if ok:
            action, changed_by = new, "risk_manager"
    if pm is not None and action == Action.BUY and pm.suggested_size in SIZE_TO_ACTION:
        new, ok = apply_downgrade(action, SIZE_TO_ACTION[pm.suggested_size])
        if ok:
            action, changed_by = new, (changed_by + "+portfolio_manager") if changed_by else "portfolio_manager"
    return action, changed_by
