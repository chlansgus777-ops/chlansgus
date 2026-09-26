"""HTTP routes. Each handler delegates to the application service and serialises domain objects."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from marketlens import __version__
from marketlens.application.codec import encode
from marketlens.application.evaluation_service import PAPER_DISCLAIMER, EvaluationService
from marketlens.application.services import MarketLensService
from marketlens.config import AGENT_PROMPT_VERSION, SCHEMA_VERSION, code_version
from marketlens.domain.enums import ACTION_KO, BULLISH_ACTIONS, Action, Horizon
from marketlens.domain.issues import compute_issue_impacts
from marketlens.domain.macro import detect_regimes, factor_moves, primary_regime
from marketlens.domain.market_calendar import last_completed_session, to_ny
from marketlens.domain.portfolio import portfolio_snapshot
from marketlens.infrastructure.db import repository as repo

router = APIRouter()
TICKER_RE = r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$"


def svc(req: Request) -> MarketLensService:
    s = req.app.state.service
    if s is None:
        raise HTTPException(503, "서비스 준비 중")
    return s


def _ticker(t: str) -> str:
    import re

    if not re.match(TICKER_RE, t):
        raise HTTPException(422, f"올바르지 않은 종목 코드: {t[:12]}")
    return t.upper()


# ---------------------------------------------------------------- system
@router.get("/system")
def system(req: Request) -> dict[str, Any]:
    s = svc(req)
    cfg = s.model_config()
    st = s.settings
    return {
        "mode": s.mode.value,
        "mock_banner": s.mode.value == "MOCK",
        "now": s.now().isoformat(),
        "versions": {
            "app_version": __version__,
            "code_version": code_version(),
            "scoring_model_version": cfg.scoring_model.version,
            "decision_model_version": cfg.decision_model_version,
            "agent_prompt_version": AGENT_PROMPT_VERSION,
            "config_version": f"{cfg.config_version}+{cfg.config_hash}",
            "schema_version": SCHEMA_VERSION,
            "sector_models_version": cfg.sector_models_version,
            "provider_version": s.provider_version(),
        },
        "llm": {"provider": getattr(s.llm, "name", "none"), "available": bool(getattr(s.llm, "available", False)), "fast_model": st.fast_model, "deep_model": st.deep_model},
        "providers": s.registry.describe(),
        "paper_trading": st.enable_paper_trading,
        "ai_committee": st.enable_ai_committee,
    }


# ---------------------------------------------------------------- opportunities / scan
def _row_summary(r: Any, s: MarketLensService | None = None, fetch_quote: bool = False) -> dict[str, Any]:
    res = r.result
    entry = res.get("entry") or {}
    er = res.get("event_risk") or {}
    nxt = (er.get("nearest") or {})
    status = s.recommendation_status(r, fetch_quote=fetch_quote) if s is not None else None
    bullish = r.final_action in {a.value for a in BULLISH_ACTIONS}
    return {
        "current_status": status.status if status else None,  # CURRENT | AGING | EXPIRED (re-judged now)
        "current_status_reason": status.reason_ko if status else None,
        "sessions_since": status.sessions_since if status else None,
        "actionable_now": bool(status and status.actionable and r.data_quality in ("FRESH", "DELAYED")) if bullish else None,
        "revalidated_price": status.revalidated_price if status else None,
        "status_problems": list(status.problems) if status else [],
        "action_ko": ACTION_KO.get(Action(r.final_action), r.final_action),
        "valuation_price_basis": res.get("valuation_price_basis"),
        "sector_known": res.get("sector_known", True),
        "id": r.id, "rank": r.rank, "ticker": r.ticker, "company": res["security"]["company_name"], "sector": r.sector,
        "sector_model": r.sector_model, "price": r.price, "session": r.session, "price_timestamp": r.price_timestamp.isoformat() if r.price_timestamp else None,
        "price_source": r.price_source, "price_quality": r.price_quality, "score": r.score, "confidence": r.confidence,
        "action": r.final_action, "deterministic_action": r.deterministic_action, "committee_status": r.committee_status,
        "ideal_entry": entry.get("ideal_entry"), "max_buy": entry.get("max_buy"), "target": entry.get("target1"),
        "stop": entry.get("stop"), "downside": entry.get("downside_pct"), "rr": entry.get("rr_at_current"),
        "catalyst": nxt.get("title"), "catalyst_date": nxt.get("event_date"), "risk": er.get("level"),
        "data_quality": r.data_quality, "mode": r.mode, "vetoes": res["decision"]["vetoes"], "as_of": r.as_of.isoformat(),
        "version": getattr(r, "version", 1) or 1, "supersedes_id": getattr(r, "supersedes_id", None),
        "issued_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
    }


@router.get("/opportunities")
def opportunities(req: Request) -> dict[str, Any]:
    s = svc(req)
    ready = s.readiness()  # "no rows" must be explainable: not ready ≠ no opportunities
    with s.sf() as ss:
        scan = repo.latest_scan(ss, mode=s.mode.value)
        if scan is None:
            return {"scan": None, "rows": [], "readiness": ready}
        rows = repo.recommendations_for_scan(ss, scan.id)
        return {"scan": {"id": scan.id, "as_of": scan.as_of.isoformat(), "mode": scan.mode, "stages": scan.stages, "excluded": scan.excluded_count, "scoring_model_version": scan.scoring_model_version},
                "rows": [_row_summary(r, s) for r in rows], "readiness": ready}


@router.post("/scan")
def run_scan(req: Request, committee: bool = True) -> dict[str, Any]:
    return encode(svc(req).run_scan(run_committee=committee))


# ---------------------------------------------------------------- stock detail
@router.get("/stocks/{ticker}")
def stock(req: Request, ticker: str, refresh: bool = False) -> dict[str, Any]:
    s = svc(req)
    t = _ticker(ticker)
    mode = s.mode.value
    if refresh:
        s.analyze(t, run_committee=False, persist=True)
    with s.sf() as ss:
        row = repo.latest_recommendation(ss, t, mode=mode)
        if row is None:
            try:
                s.analyze(t, run_committee=False, persist=True)
            except KeyError:
                raise HTTPException(404, f"{t}: 분석 시점의 유니버스에 없는 종목") from None
            row = repo.latest_recommendation(ss, t, mode=mode)
        if row is None:
            raise HTTPException(404, f"{t}: 분석 결과 없음")
        com = repo.committee_for(ss, row.id)
        history = [{"id": h.id, "as_of": h.as_of.isoformat(), "score": h.score, "action": h.final_action} for h in repo.recommendation_history(ss, t, 30, mode=mode)]
        bars = (row.inputs or {}).get("bars") or []
        return {
            "recommendation": _row_summary(row, s, fetch_quote=True),
            "analysis": row.result,
            "price_history": [{"day": b["day"], "close": b["close"]} for b in bars[-130:]],
            "committee": com.payload if com else None,
            "committee_recommendation_id": com.recommendation_id if com else None,  # the UI shows it only for this version
            "history": history,
            "versions": {"scoring": row.scoring_model_version, "decision": row.decision_model_version, "prompt": row.agent_prompt_version, "config": row.config_version,
                         "provider": row.provider_version, "schema": row.schema_version, "code": row.code_version, "app": row.app_version, "llm_models": row.llm_model_ids,
                         "input_fingerprint": row.input_fingerprint},
        }


@router.post("/recommendations/{rec_id}/committee")
def committee(req: Request, rec_id: int) -> dict[str, Any]:
    return svc(req).committee_for_recommendation(rec_id)


@router.get("/recommendations/{rec_id}/replay")
def replay(req: Request, rec_id: int) -> dict[str, Any]:
    o = svc(req).replay_recommendation(rec_id)
    return {"matches": o.matches, "fingerprint_matches": o.fingerprint_matches, "original_score": o.original_score, "replay_score": o.replay_score, "original_action": o.original_action, "replay_action": o.replay_action}


# ---------------------------------------------------------------- market / macro / issues / calendar
@router.get("/macro")
def macro(req: Request) -> dict[str, Any]:
    s = svc(req)
    snap, missing = s.data.macro_snapshot(s.now())
    if snap is None:
        return {"available": False, "reason": missing, "series": {}, "regimes": [], "primary_regime": "Unknown", "factor_moves": []}
    regs = detect_regimes(snap)
    return {"available": True, "as_of": snap.as_of.isoformat(), "series": encode(snap.series), "regimes": encode(regs), "primary_regime": primary_regime(regs), "factor_moves": encode(factor_moves(snap)), "yield_curve_2s10s": snap.yield_curve_2s10s}


def _ctx(s: MarketLensService) -> Any:
    if s.last_scan_context is None:
        with s.sf() as ss:
            s.last_scan_context = s.scanner(s.model_config(), ss).build_context(s.now())
    return s.last_scan_context


@router.get("/issues")
def issues(req: Request) -> dict[str, Any]:
    s = svc(req)
    ctx = _ctx(s)
    if ctx.issues is None:
        return {"available": False, "reason": ctx.news_missing, "issues": []}
    out = []
    for i in ctx.issues.issues:
        impacts = compute_issue_impacts(i, ctx.graph, None, s.base_cfg.impact)
        out.append({
            "issue": encode(i),
            "affected_stocks": [{"ticker": x.ticker, "hops": x.hops, "swing": x.at(Horizon.SWING).impact_score} for x in impacts[:15]],
            "affected_sectors": sorted({ctx.securities[x.ticker].sector for x in impacts if x.ticker in ctx.securities}),
        })
    return {"available": True, "issues": out, "injection_flags": ctx.issues.injection_flags}


@router.get("/issues/{issue_id}")
def issue_detail(req: Request, issue_id: str) -> dict[str, Any]:
    s = svc(req)
    ctx = _ctx(s)
    iss = next((i for i in (ctx.issues.issues if ctx.issues else []) if i.issue_id == issue_id), None)
    if iss is None:
        raise HTTPException(404, issue_id)
    impacts = compute_issue_impacts(iss, ctx.graph, None, s.base_cfg.impact)
    return {"issue": encode(iss), "impacts": encode(impacts), "direct": [x.ticker for x in impacts if x.hops == 0], "indirect": [x.ticker for x in impacts if x.hops > 0]}


@router.get("/calendar")
def calendar(req: Request, days: int = 45) -> dict[str, Any]:
    s = svc(req)
    days = max(1, min(days, 365))
    d = to_ny(s.now()).date()
    f = s.data.events(d - timedelta(days=1), d + timedelta(days=days))
    evs = sorted(f.value or [], key=lambda e: e.event_date)
    return {"available": f.value is not None, "reason": f.error, "events": [encode(e) | {"days_until": e.days_until(d)} for e in evs if (not e.affected or e.importance >= 0.8)][:300]}


# ---------------------------------------------------------------- dashboard
@router.get("/dashboard")
def dashboard(req: Request) -> dict[str, Any]:
    s = svc(req)
    opp = opportunities(req)
    rows = opp["rows"]
    top = [r for r in rows if r["action"] in {a.value for a in BULLISH_ACTIONS}][:8] or rows[:8]
    risks = []
    for r in rows:
        if r["vetoes"]:
            risks.append({"ticker": r["ticker"], "text": ", ".join(r["vetoes"])})
        elif r["risk"] in ("HIGH", "EXTREME"):
            risks.append({"ticker": r["ticker"], "text": f"이벤트 위험 {r['risk']}"})
    m = macro(req)
    cal = calendar(req, 21)
    changes = []
    for r in rows:
        with s.sf() as ss:
            rec = repo.get_recommendation(ss, r["id"])
            items = [c for c in ((rec.result or {}).get("changes") or []) if c.get("kind") == "action"] if rec else []
        if items:
            changes.append({"ticker": r["ticker"], "text": items[0]["text"], "action": r["action"]})
    alerts = []
    with s.sf() as ss:
        pf = s.portfolio(ss)
        for w in repo.watchlist(ss):
            rec = repo.latest_recommendation(ss, w.ticker, mode=s.mode.value)
            if rec is None:
                alerts.append({"ticker": w.ticker, "level": "info", "text": "아직 분석하지 않은 관심 종목입니다."})
                continue
            row = _row_summary(rec, s)
            if row["action"] in {a.value for a in BULLISH_ACTIONS} and row["actionable_now"]:
                alerts.append({"ticker": w.ticker, "level": "positive", "text": f"{row['action_ko']} 신호 — 최대 매수가 {row['max_buy']}달러 이하에서 유효"})
            elif row["current_status"] != "CURRENT":
                alerts.append({"ticker": w.ticker, "level": "info", "text": "마지막 분석이 오래되었습니다. 다시 분석해 보세요."})
            elif row["vetoes"]:
                alerts.append({"ticker": w.ticker, "level": "warning", "text": "주의: " + ", ".join(row["vetoes"])})
    acct = EvaluationService(s).paper_account()
    perf = None
    if acct and acct.get("equity"):
        last = acct["equity"][-1][1]
        perf = {"equity": last, "starting_capital": acct.get("starting_capital"), "return": (last / acct["starting_capital"] - 1) if acct.get("starting_capital") else None,
                "max_drawdown": acct.get("max_drawdown"), "as_of": acct.get("as_of"), "curve": [v for _, v in acct["equity"][-60:]]}
    return {
        "performance": perf,
        "recommendation_changes": changes[:8],
        "watchlist_alerts": alerts[:8],
        "scan": opp["scan"],
        "regime": {"primary": m.get("primary_regime"), "readings": [x for x in m.get("regimes", []) if x.get("active")]},
        "top_opportunities": top,
        "major_risks": risks[:10],
        "upcoming_catalysts": cal["events"][:10],
        "portfolio": {"holdings": len(pf.holdings), "cash": pf.cash},
        "provider_health": [h.as_dict() for h in s.health.all()],
        "readiness": opp["readiness"],
    }


# ---------------------------------------------------------------- portfolio & watchlist
class HoldingIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    quantity: float = Field(ge=0)
    cost_basis: float = Field(ge=0)


class PortfolioIn(BaseModel):
    cash: float | None = Field(default=None, ge=0)
    holdings: list[HoldingIn] = Field(default_factory=list)


@router.get("/portfolio")
def portfolio(req: Request) -> dict[str, Any]:
    s = svc(req)
    with s.sf() as ss:
        pf = s.portfolio(ss)
    end = last_completed_session(s.now())
    start = end - timedelta(days=260)
    closes = {h.ticker: {b.day: b.close for b in (s.data.bars(h.ticker, start, end).value or []) if b.day <= end} for h in pf.holdings}
    bench = {b.day: b.close for b in (s.data.bars("SPY", start, end).value or []) if b.day <= end}
    snap = portfolio_snapshot(pf, closes, bench or None)
    return encode(snap) | {"currency": "USD", "note": "MarketLens는 주문을 넣지 않습니다. 평가금액은 모든 종목을 같은 거래일 종가로 계산합니다."}


@router.put("/portfolio")
def set_portfolio(req: Request, body: PortfolioIn) -> dict[str, Any]:
    s = svc(req)
    with s.sf() as ss:
        if body.cash is not None:
            repo.set_setting(ss, "portfolio_cash", str(body.cash))
        for h in body.holdings:
            repo.upsert_holding(ss, _ticker(h.ticker), h.quantity, h.cost_basis)
        ss.commit()
    return portfolio(req)


@router.get("/watchlist")
def get_watchlist(req: Request) -> list[dict[str, Any]]:
    s = svc(req)
    with s.sf() as ss:
        out = []
        for w in repo.watchlist(ss):
            rec = repo.latest_recommendation(ss, w.ticker, mode=s.mode.value)
            out.append({"ticker": w.ticker, "note": w.note, "added_at": w.added_at.isoformat(), "latest": _row_summary(rec, s) if rec else None})
        return out


@router.post("/watchlist/{ticker}")
def add_watch(req: Request, ticker: str) -> dict[str, str]:
    s = svc(req)
    with s.sf() as ss:
        repo.add_watch(ss, _ticker(ticker))
        ss.commit()
    return {"status": "ok"}


@router.delete("/watchlist/{ticker}")
def del_watch(req: Request, ticker: str) -> dict[str, str]:
    s = svc(req)
    with s.sf() as ss:
        repo.remove_watch(ss, _ticker(ticker))
        ss.commit()
    return {"status": "ok"}


# ---------------------------------------------------------------- evaluation & calibration
@router.get("/performance")
def performance(req: Request, period: str = "all") -> dict[str, Any]:
    days = {"30d": 30, "90d": 90, "1y": 365}.get(period)
    return EvaluationService(svc(req)).performance(days)


@router.post("/evaluation/run")
def run_evaluation(req: Request) -> dict[str, Any]:
    ev = EvaluationService(svc(req))
    return {"outcomes_written": ev.update_outcomes(), "paper": ev.update_paper()}


@router.get("/paper/account")
def paper_account(req: Request) -> dict[str, Any]:
    acct = EvaluationService(svc(req)).paper_account()
    return {"available": acct is not None, "account": acct, "disclaimer": PAPER_DISCLAIMER}


@router.post("/sync")
def sync(req: Request) -> dict[str, Any]:
    return encode(svc(req).sync_market())


@router.post("/calibration/run")
def run_calibration(req: Request) -> dict[str, Any]:
    return EvaluationService(svc(req)).calibrate()


@router.post("/calibration/promote")
def promote_calibration(req: Request) -> dict[str, Any]:
    return EvaluationService(svc(req)).promote_shadow()


@router.get("/calibration")
def calibration(req: Request) -> dict[str, Any]:
    s = svc(req)
    with s.sf() as ss:
        return {
            "runs": [{"id": r.id, "status": r.status, "candidate": r.candidate_version, "created_at": r.created_at.isoformat(), "payload": r.payload} for r in repo.calibration_runs(ss)],
            "models": [{"version": m.version, "status": m.status, "weights": m.weights, "parent": m.parent_version, "shadow_started": m.shadow_started.isoformat() if m.shadow_started else None} for m in repo.model_versions(ss)],
            "production_weights": dict(s.model_config().scoring_model.weights),
            "production_version": s.model_config().scoring_model.version,
        }


# ---------------------------------------------------------------- health & settings
@router.get("/readiness")
def readiness(req: Request) -> dict[str, Any]:
    """Can today's recommendations be trusted? FULL / LIMITED / PAPER ONLY / NOT READY, with progress."""
    return svc(req).readiness()


@router.get("/health")
def health(req: Request) -> dict[str, Any]:
    s = svc(req)
    with s.sf() as ss:
        usage = repo.llm_usage(ss)
    return {
        "providers": [h.as_dict() for h in s.health.all()],
        "configured": s.registry.describe(),
        "llm": {"provider": getattr(s.llm, "name", "none"), "available": bool(getattr(s.llm, "available", False)), "status": "HEALTHY" if getattr(s.llm, "available", False) else "DOWN", "usage": usage},
    }


@router.get("/settings")
def settings(req: Request) -> dict[str, Any]:
    s = svc(req)
    cfg = s.model_config()
    st = s.settings
    return {
        "mode": st.mode.value,
        "keys_configured": {"FINNHUB_API_KEY": bool(st.finnhub_api_key), "FRED_API_KEY": bool(st.fred_api_key), "POLYGON_API_KEY": bool(st.polygon_api_key), "FINRA_API_KEY": bool(st.finra_api_key), "ANTHROPIC_API_KEY": bool(st.anthropic_api_key), "OPENAI_API_KEY": bool(st.openai_api_key), "SEC_USER_AGENT": bool(st.sec_user_agent)},
        "weights": dict(cfg.scoring_model.weights),
        "decision": encode(cfg.decision),
        "entry": encode(cfg.entry),
        "scanner": encode(cfg.scanner),
        "calibration": encode(cfg.calibration),
        "sector_models": [{"id": m.model_id, "name": m.name, "rationale": m.rationale, "primary_multiple": m.primary_multiple, "fundamental": [encode(r) for r in m.fundamental_rules], "valuation": [encode(r) for r in m.valuation_rules]} for m in cfg.sector_models],
        "note": "API 키 등 비밀값은 API로 절대 반환되지 않습니다. .env 파일 또는 OS 자격 증명 관리자에서 설정하세요.",
    }
