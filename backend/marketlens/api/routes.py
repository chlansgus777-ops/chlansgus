"""HTTP routes. Each handler delegates to the application service and serialises domain objects."""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from marketlens import __version__
from marketlens.application.codec import encode
from marketlens.application.evaluation_service import PAPER_DISCLAIMER, EvaluationService
from marketlens.application.services import CALENDAR_VIEW_DAYS, MarketLensService
from marketlens.config import AGENT_PROMPT_VERSION, SCHEMA_VERSION, code_version
from marketlens.application.brief import build_brief
from marketlens.domain.enums import ACTION_KO, BULLISH_ACTIONS, Action, Horizon
from marketlens.domain.issues import compute_issue_impacts
from marketlens.domain.macro import detect_regimes, factor_moves, primary_regime
from marketlens.domain.market_calendar import classify_session, last_completed_session, to_ny
from marketlens.domain.portfolio import portfolio_snapshot
from marketlens.infrastructure.db import repository as repo

router = APIRouter()
TICKER_RE = r"^[A-Za-z][A-Za-z0-9.\-]{0,9}$"


def svc(req: Request) -> MarketLensService:
    s = req.app.state.service
    if s is None:
        raise HTTPException(503, "서비스 준비 중")
    return s


WATCH_STATUS_KO = {
    "AGING": "마지막 분석 후 거래일이 지났습니다. 다시 분석해 보세요.",
    "EXPIRED": "마지막 분석이 만료됐습니다. 다시 분석해 보세요.",
    "NEEDS_REVALIDATION": "분석 후 가격이 움직였을 수 있습니다. 지금 가격으로 다시 확인하세요.",
    "PLAN_INVALIDATED": "지금 가격이 분석 때 계획 범위를 벗어났습니다.",
}
DATA_STATE_VETOES = frozenset({"STALE_PRICE", "STALE_CORE_DATA", "MISSING_CORE_DATA", "INSUFFICIENT_MODEL_COVERAGE", "SEVERE_DATA_CONFLICT"})


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
    now = s.now()
    from marketlens.config import keychain_backend

    return {
        "mode": s.mode.value,
        "mock_banner": s.mode.value == "MOCK",
        "keychain": keychain_backend(),  # where entered keys are kept: the OS keychain, or (None) the private .env
        "now": now.isoformat(),
        # display only: the US session right now from the exchange calendar (holidays, early closes) — the UI shows it
        # instead of guessing from the clock
        "market": {"session": classify_session(now).value, "ny_time": to_ny(now).isoformat(), "last_completed_session": last_completed_session(now).isoformat()},
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
    lv = s.levels_now(r) if s is not None else None  # after a split: the levels on today's share basis
    status = s.recommendation_status(r, fetch_quote=fetch_quote, levels=lv) if s is not None else None
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
        "sector_model": r.sector_model, "price": lv["price"] if lv else r.price, "split_factor_since": lv["split_factor"] if lv else 1.0, "session": r.session, "scan_session": classify_session(r.as_of).value if r.as_of else None, "price_timestamp": r.price_timestamp.isoformat() if r.price_timestamp else None,
        "price_source": r.price_source, "price_quality": r.price_quality, "score": r.score, "confidence": r.confidence,
        "action": r.final_action, "deterministic_action": r.deterministic_action, "committee_status": r.committee_status,
        "ideal_entry": lv["ideal_entry"] if lv else entry.get("ideal_entry"), "max_buy": lv["max_buy"] if lv else entry.get("max_buy"),
        "target": lv["target1"] if lv else entry.get("target1"), "stop": lv["stop"] if lv else entry.get("stop"), "downside": entry.get("downside_pct"), "rr": entry.get("rr_at_current"),
        # the rest of the plan on the same (today's) share basis, so a screen never mixes it with the analysis snapshot (review 2026-09-28 F03)
        "target2": lv["target2"] if lv else entry.get("target2"), "buy_zone_low": lv["acceptable_low"] if lv else entry.get("acceptable_low"),
        "buy_zone_high": lv["acceptable_high"] if lv else entry.get("acceptable_high"), "add_zone_low": lv["add_zone_low"] if lv else entry.get("add_zone_low"),
        "add_zone_high": lv["add_zone_high"] if lv else entry.get("add_zone_high"),
        "catalyst": nxt.get("title"), "catalyst_date": nxt.get("event_date"), "risk": er.get("level"),
        "data_quality": r.data_quality, "mode": r.mode, "vetoes": res["decision"]["vetoes"], "as_of": r.as_of.isoformat(),
        "version": getattr(r, "version", 1) or 1, "supersedes_id": getattr(r, "supersedes_id", None),
        "issued_at": r.created_at.isoformat() if getattr(r, "created_at", None) else None,
    }


def _overlay_live(row: dict[str, Any], live: dict[str, Any] | None) -> None:
    """The live re-judgement (MarketLensService.live_rejudge) over a stored row: the same analysis with the current
    price. The stored analysis is kept under ``stored`` so the screen can say what changed."""
    if not live:
        return
    row["stored"] = {k: row.get(k) for k in ("action", "score", "price", "max_buy", "data_quality", "vetoes")}
    row.update({"action": live["action"], "action_ko": ACTION_KO.get(Action(live["action"]), live["action"]), "score": live["score"],
                "price": live["price"], "price_timestamp": live["quote_ts"], "price_source": live["source"], "session": live["session"],
                "price_quality": live.get("price_quality", row.get("price_quality")),
                "data_quality": live["data_quality"], "vetoes": live["vetoes"], "max_buy": live["max_buy"], "ideal_entry": live["ideal_entry"],
                "stop": live["stop"], "target": live["target1"], "target2": live["target2"], "rr": live["rr"], "downside": live["downside"],
                "buy_zone_low": live["buy_zone_low"], "buy_zone_high": live["buy_zone_high"], "live_at": live["at"]})
    bullish = live["action"] in {a.value for a in BULLISH_ACTIONS}
    row["actionable_now"] = (live["data_quality"] in ("FRESH", "DELAYED")) if bullish else None
    row["current_status"], row["current_status_reason"] = "CURRENT", "실시간 가격으로 다시 판정"


def _scan_rows(s: MarketLensService, ss: Any) -> tuple[Any, list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """The latest scan's current recommendations: (scan, row summaries, stored analysis by id) from ONE query."""
    scan = s.shown_scan(ss)  # while a scan is being saved, the previous complete one stays on screen
    if scan is None:
        return None, [], {}
    recs = repo.recommendations_for_scan(ss, scan.id)
    # a name analysed again after the scan ("이 종목만 다시 분석", an automatic re-analysis) shows that newer analysis in
    # its scan place (owner 2026-09-29: the stock page said WATCH, the list still the scan's 데이터 부족)
    newer = repo.newer_single_analyses(ss, [r.ticker for r in recs], scan.as_of, s.mode.value)
    rows, results = [], {}
    for r in recs:
        n = newer.get(r.ticker)
        use = n if n is not None else r
        row = _row_summary(use, s)
        if n is not None:
            row["rank"] = r.rank
            row["reanalyzed_at"] = n.as_of.isoformat()
            row["scan_action"] = r.final_action
        _overlay_live(row, s.rejudged(use.id))
        rows.append(row)
        results[use.id] = use.result or {}
    # the live order (owner 2026-09-29: a name that turns good climbs at once): judged rows by score, data-insufficient last
    rows.sort(key=lambda x: (x["action"] == Action.DATA_INSUFFICIENT.value, -(x["score"] or 0), x["rank"] or 0))
    for i, row in enumerate(rows, start=1):
        row["scan_rank"], row["rank"] = row["rank"], i
    return scan, rows, results


def _scan_head(scan: Any) -> dict[str, Any] | None:
    return None if scan is None else {"id": scan.id, "as_of": scan.as_of.isoformat(), "mode": scan.mode, "stages": scan.stages, "excluded": scan.excluded_count,
                                      "scoring_model_version": scan.scoring_model_version}


@router.get("/opportunities")
def opportunities(req: Request) -> dict[str, Any]:
    s = svc(req)
    ready = s.readiness_view()  # "no rows" must be explainable: not ready ≠ no opportunities (last count, never a wait)
    with s.sf() as ss:
        scan, rows, _ = _scan_rows(s, ss)
    return {"scan": _scan_head(scan), "rows": rows, "readiness": ready}


@router.get("/stocks/{ticker}/live")
def stock_live(req: Request, ticker: str) -> dict[str, Any]:
    """The stock page's live re-judgement (its stored analysis with the current price), polled every second."""
    return {"live": svc(req).live_for(_ticker(ticker))}


@router.get("/opportunities/live")
def opportunities_live(req: Request) -> dict[str, Any]:
    """The list's live re-judgements only (memory, no database) — the screens poll this every second."""
    return svc(req).live_board()


@router.post("/scan")
def run_scan(req: Request, committee: bool = True) -> dict[str, Any]:
    from marketlens.application.services import ScanRefused

    try:
        return encode(svc(req).run_scan(run_committee=committee))
    except ScanRefused as e:
        raise HTTPException(409, str(e)) from None


# ---------------------------------------------------------------- stock detail
@router.get("/scan/status")
def scan_status(req: Request) -> dict[str, Any]:
    """The last scan: progress (COMPLETE / RUNNING / INTERRUPTED with how many results were saved) and coverage —
    how much of the market was judged, why the rest was left out, the missing-data rate and the AI cost."""
    return svc(req).scan_status()


@router.get("/stocks/{ticker}")
def stock(req: Request, ticker: str, refresh: bool = False) -> dict[str, Any]:
    s = svc(req)
    t = _ticker(ticker)
    # analysing stores a recommendation: a GET may do that only for the app itself (the client header a cross-site
    # image, link or no-cors fetch cannot send) — round 10 security invariant; without it the stored result is read
    may_write = req.headers.get("x-marketlens-client") is not None
    if refresh and not may_write:
        raise HTTPException(403, "재분석은 앱 화면에서만 요청할 수 있습니다(X-MarketLens-Client 헤더 필요)")
    if refresh:
        s.analyze(t, run_committee=False, persist=True)
    with s.sf() as ss:
        row = s.latest_company_recommendation(ss, t)
        if row is None and not may_write:
            raise HTTPException(404, f"{t}: 저장된 분석이 없습니다 — 앱 화면에서 분석을 요청하세요")
        if row is None:
            try:
                s.analyze(t, run_committee=False, persist=True)
            except KeyError:
                raise HTTPException(404, f"{t}: 분석 시점의 유니버스에 없는 종목") from None
            row = s.latest_company_recommendation(ss, t)
        if row is None:
            raise HTTPException(404, f"{t}: 분석 결과 없음")
        com = repo.committee_for(ss, row.id)
        history = [{"id": h.id, "as_of": h.as_of.isoformat(), "score": h.score, "action": h.final_action} for h in s.company_recommendations(ss, t, limit=30, light=True)]
        bars = (row.inputs or {}).get("bars") or []
        quote_pending = s.quote_soon(t, wait=STORE_FIRST_WAIT)  # the current-price re-check never holds the page longer
        summary = _row_summary(row, s) | {"quote_pending": quote_pending}
        return {
            "recommendation": summary,
            # the five-question reading of this analysis, on today's share basis (product overhaul 2026-09-28)
            "brief": build_brief(row.result, row.inputs if isinstance(row.inputs, dict) else None, s.levels_now(row), s.base_cfg.decision, row.final_action, row.score,
                                 _issue_titles(s)),
            "position_plan": _position_plan(s, ss, row, summary),
            "analysis": row.result,
            # on today's share basis like the plan drawn over it: a split after the analysis divides the stored closes too
            "price_history": [{"day": b["day"], "close": b["close"] / (summary.get("split_factor_since") or 1.0)} for b in bars[-130:]],
            "committee": com.payload if com else None,
            "committee_recommendation_id": com.recommendation_id if com else None,  # the UI shows it only for this version
            "history": history,
            "versions": {"scoring": row.scoring_model_version, "decision": row.decision_model_version, "prompt": row.agent_prompt_version, "config": row.config_version,
                         "provider": row.provider_version, "schema": row.schema_version, "code": row.code_version, "app": row.app_version, "llm_models": row.llm_model_ids,
                         "input_fingerprint": row.input_fingerprint},
        }


def _issue_titles(s: MarketLensService) -> dict[str, str]:
    """Issue headlines of the shared market context (the stored analysis keeps only the issue ids)."""
    ctx = s.last_scan_context
    issues = getattr(getattr(ctx, "issues", None), "issues", None) or []
    return {i.issue_id: i.title for i in issues}


def _position_plan(s: MarketLensService, ss: Any, row: Any, summary: dict[str, Any]) -> dict[str, Any]:
    """Dollars and whole shares for a buy recommendation, from the user's portfolio value (cash + holdings at the
    last close). Without an entered portfolio the screen says so instead of guessing an account size."""
    from marketlens.domain.portfolio import position_plan
    from marketlens.domain.sizing import recommendation_size_cap

    if row.final_action not in {a.value for a in BULLISH_ACTIONS}:
        return {"available": False, "reason": "매수 판정이 아니어서 매수 수량을 계산하지 않음"}
    # an order-sized quantity only for a recommendation that holds NOW (independent review 2026-09-28 F04): an expired,
    # re-judged or unchecked plan is a record of the analysis, not something to buy — the screen shows why instead
    if summary.get("actionable_now") is not True:
        why = summary.get("current_status_reason") or "현재 가격 기준으로 다시 확인되지 않음"
        return {"available": False, "reason": f"지금 실행할 수 있는 매수 추천이 아니어서 수량을 계산하지 않음 — {why}", "status": summary.get("current_status")}
    pf = s.portfolio(ss)
    end = last_completed_session(s.now())
    series, _ = _view_bars(s, [h.ticker for h in pf.holdings], end - timedelta(days=10), end)
    closes = {h.ticker: {b.day: b.close for b in series.get(h.ticker, []) if b.day <= end} for h in pf.holdings}
    entered = repo.get_setting(ss, "portfolio_cash", "") not in ("", None) or bool(pf.holdings)  # a default cash figure is not the user's account
    snap = portfolio_snapshot(pf, closes) if entered else None
    if snap is not None and snap.valuation_status == "UNAVAILABLE":  # a cash-only total is not the account (review 2026-09-28 F10)
        return {"available": False, "reason": "보유 종목을 같은 거래일 종가로 평가할 수 없어 포트폴리오 금액을 모릅니다 — 매수 금액을 계산하지 않음"}
    nav = snap.nav if snap is not None else None
    price = summary.get("revalidated_price") or summary.get("price")  # the re-checked current price when there is one
    current = next((h.quantity * price for h in pf.holdings if h.ticker == row.ticker), 0.0) if price else 0.0
    size_cap = recommendation_size_cap(row)  # every limit: decision, portfolio review, AI portfolio manager (review 2026-09-28 F01/F05)
    p = position_plan(row.final_action, size_cap, nav, price, summary.get("stop"), current, s.model_config().portfolio)  # stop on today's share basis
    if p is None:
        why = ("포트폴리오(현금·보유 종목)를 입력하면 매수 금액과 수량을 계산합니다" if not nav
               else "비중 한도(WATCH)로 새 매수 금액이 없음" if size_cap == "WATCH" else "현재가가 없어 계산하지 않음")
        return {"available": False, "reason": why}
    return {"available": True, "nav": round(nav or 0, 2)} | encode(p)


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
    return _macro_body(svc(req).macro_view(wait=VIEW_FIRST_WAIT))


VIEW_FIRST_WAIT = 1.5  # seconds a screen waits for a provider when it has nothing yet; then it says "loading"
STORE_FIRST_WAIT = 0.4  # stored history / a quote: answered in milliseconds when present — past this it is a provider call


def _view_meta(v: Any) -> dict[str, Any]:
    """When the shown data was fetched and whether a newer fetch is running — a cached answer is never shown as new."""
    return {"fetched_at": v.computed_at.isoformat() if v.computed_at else None, "refreshing": v.refreshing, "refresh_error": v.error if v.ready else None}


def _macro_body(v: Any) -> dict[str, Any]:
    snap = v.value
    if snap is None:
        pending = v.refreshing
        return {"available": False, "pending": pending, "reason": "거시 지표를 불러오는 중" if pending else (v.error or "거시 데이터 없음"),
                "series": {}, "regimes": [], "primary_regime": "Unknown", "factor_moves": []} | _view_meta(v)
    regs = detect_regimes(snap)
    return {"available": True, "as_of": snap.as_of.isoformat(), "series": encode(snap.series), "regimes": encode(regs), "primary_regime": primary_regime(regs),
            "factor_moves": encode(factor_moves(snap)), "yield_curve_2s10s": snap.yield_curve_2s10s} | _view_meta(v)


def _ctx(s: MarketLensService) -> tuple[Any, bool]:
    """The newest shared market context at once; an older one than CONTEXT_MAX_AGE is rebuilt in the background
    (review 2026-09-28 F06) and the answer says so — the screen never waits for the news collection."""
    return s.context_view(wait=VIEW_FIRST_WAIT)


@router.get("/issues")
def issues(req: Request) -> dict[str, Any]:
    s = svc(req)
    ctx, rebuilding = _ctx(s)
    if ctx is None:
        return {"available": False, "pending": rebuilding, "reason": "시장 이슈를 모으는 중" if rebuilding else "시장 이슈를 만들지 못함", "issues": [], "refreshing": rebuilding}
    meta = {"as_of": ctx.as_of.isoformat(), "refreshing": rebuilding}
    if ctx.issues is None:
        return {"available": False, "reason": ctx.news_missing, "issues": []} | meta
    out = []
    for i in ctx.issues.issues:
        impacts = compute_issue_impacts(i, ctx.graph, None, s.base_cfg.impact)
        out.append({
            "issue": encode(i),
            "affected_stocks": [{"ticker": x.ticker, "hops": x.hops, "swing": x.at(Horizon.SWING).impact_score} for x in impacts[:15]],
            "affected_sectors": sorted({ctx.securities[x.ticker].sector for x in impacts if x.ticker in ctx.securities}),
        })
    return {"available": True, "issues": out, "injection_flags": ctx.issues.injection_flags} | meta


@router.get("/issues/{issue_id}")
def issue_detail(req: Request, issue_id: str) -> dict[str, Any]:
    s = svc(req)
    ctx, _ = _ctx(s)
    iss = None if ctx is None else next((i for i in (ctx.issues.issues if ctx.issues else []) if i.issue_id == issue_id), None)
    if iss is None:
        raise HTTPException(404, issue_id)
    impacts = compute_issue_impacts(iss, ctx.graph, None, s.base_cfg.impact)
    return {"issue": encode(iss), "impacts": encode(impacts), "direct": [x.ticker for x in impacts if x.hops == 0], "indirect": [x.ticker for x in impacts if x.hops > 0]}


@router.get("/calendar")
def calendar(req: Request, days: int = 45) -> dict[str, Any]:
    s = svc(req)
    days = max(1, min(days, 365))
    return _calendar_body(s, s.calendar_view(days, wait=VIEW_FIRST_WAIT), days)


def _calendar_body(s: MarketLensService, v: Any, days: int) -> dict[str, Any]:
    d = to_ny(s.now()).date()
    if v.value is None:
        pending = v.refreshing
        return {"available": False, "pending": pending, "reason": "일정을 불러오는 중" if pending else v.error, "events": []} | _view_meta(v)
    evs = sorted((e for e in v.value if e.event_date <= d + timedelta(days=days)), key=lambda e: e.event_date)
    return {"available": True, "reason": None, "events": [encode(e) | {"days_until": e.days_until(d)} for e in evs if (not e.affected or e.importance >= 0.8)][:300]} | _view_meta(v)


# ---------------------------------------------------------------- dashboard
def _card_facts(res: dict[str, Any]) -> dict[str, Any]:
    """One stored reason and one stored risk for a dashboard card, read from the analysis saved with the recommendation
    (display only: no new analysis, no quote fetch, nothing recomputed).

    key_reason: the first positive reason of the component that added the most weighted points (the entry/price-plan
    component is left out — the card shows the plan itself). key_risk: the first hard veto, else a HIGH/EXTREME event
    risk, else the first negative reason of the heaviest component; None when the analysis recorded none."""
    comps = [c for c in ((res.get("scorecard") or {}).get("components") or []) if c.get("available")]
    heavy = sorted(comps, key=lambda c: -(float(c.get("weight") or 0) * float(c.get("subscore") or 0)))
    reason = next((r["text"] for c in heavy if c.get("name") != "entry_rr" for r in (c.get("reasons") or []) if (r.get("sign") or 0) > 0), None)
    dec, er = res.get("decision") or {}, res.get("event_risk") or {}
    risk: dict[str, Any] | None = None
    if dec.get("vetoes"):
        risk = {"kind": "veto", "code": dec["vetoes"][0], "text": None}
    elif er.get("level") in ("HIGH", "EXTREME"):
        risk = {"kind": "event", "code": er["level"], "text": "; ".join(er.get("reasons") or []) or ((er.get("nearest") or {}).get("title"))}
    else:
        neg = next((r["text"] for c in sorted(comps, key=lambda c: -float(c.get("weight") or 0)) for r in (c.get("reasons") or []) if (r.get("sign") or 0) < 0), None)
        if neg:
            risk = {"kind": "negative", "code": None, "text": neg}
    return {"key_reason": reason, "key_risk": risk}


@router.get("/dashboard")
def dashboard(req: Request) -> dict[str, Any]:
    """The home screen from stored data only: candidates, portfolio and watchlist at once; the macro regime and the
    upcoming events are the last fetched ones (with their fetch time) while a background refresh runs — a slow or
    failing provider never holds the screen (owner report 2026-09-28: 30 s+ for FRED and the calendar in turn)."""
    s = svc(req)
    ready = s.readiness_view(wait=0.0)
    # at most a brief wait, and only by the request that starts a fetch (a fast provider answers within it); after that
    # the cached value or "loading" — a slow provider never holds the home screen
    mv, cv = s.macro_view(wait=0.3), s.calendar_view(CALENDAR_VIEW_DAYS, wait=0.3)
    bullish_set = {a.value for a in BULLISH_ACTIONS}
    alerts = []
    with s.sf() as ss:
        scan, rows, results = _scan_rows(s, ss)
        pf = s.portfolio(ss)
        cash_entered = repo.get_setting(ss, "portfolio_cash") is not None
        for w in repo.watchlist(ss):
            rec = s.latest_company_recommendation(ss, w.ticker)
            if rec is None:
                alerts.append({"ticker": w.ticker, "level": "info", "text": "아직 분석하지 않은 관심 종목입니다."})
                continue
            row = _row_summary(rec, s)
            if row["action"] in bullish_set and row["actionable_now"]:
                alerts.append({"ticker": w.ticker, "level": "positive", "text": f"{row['action_ko']} 신호 — 최대 매수가 {row['max_buy']}달러 이하에서 유효"})
            elif row["action"] == Action.DATA_INSUFFICIENT.value:  # says why, never "old" for a fresh analysis
                why = "현재가가 최신이 아니어서" if "STALE_PRICE" in row["vetoes"] else "핵심 데이터가 부족해"
                alerts.append({"ticker": w.ticker, "level": "info", "text": f"판단 보류 — {why} 판단하지 않았습니다."})
            elif row["current_status"] != "CURRENT":
                alerts.append({"ticker": w.ticker, "level": "info", "text": WATCH_STATUS_KO.get(row["current_status"], "마지막 분석을 지금 가격으로 다시 확인해야 합니다.")})
            elif row["vetoes"]:
                alerts.append({"ticker": w.ticker, "level": "warning", "text": "주의: " + ", ".join(row["vetoes"])})
            else:  # nothing to act on: still listed, so a watched name never reads as "no watchlist"
                alerts.append({"ticker": w.ticker, "level": "info", "text": f"{row['action_ko']} · 새 신호 없음"})
    top = [r for r in rows if r["action"] in bullish_set][:8] or rows[:8]
    risks = []
    for r in rows:
        # missing / stale / conflicting data is a data state (explained with the list), not a risk of the company
        vetoes = [v for v in r["vetoes"] if v not in DATA_STATE_VETOES]
        if vetoes:
            risks.append({"ticker": r["ticker"], "text": ", ".join(vetoes)})
        elif r["risk"] in ("HIGH", "EXTREME"):
            risks.append({"ticker": r["ticker"], "text": f"이벤트 위험 {r['risk']}"})
    changes = []
    top_ids = {r["id"] for r in top}
    for r in rows:
        res = results.get(r["id"]) or {}
        items = [c for c in (res.get("changes") or []) if c.get("kind") == "action"]
        if items:
            changes.append({"ticker": r["ticker"], "text": items[0]["text"], "action": r["action"]})
        if r["id"] in top_ids:
            r.update(_card_facts(res))
    m = _macro_body(mv)
    cal = _calendar_body(s, cv, 21)
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
        "scan": _scan_head(scan),
        "regime": {"primary": m.get("primary_regime"), "readings": [x for x in m.get("regimes", []) if x.get("active")]},
        # each outside section says whether it is loaded, when it was fetched and whether a refresh runs
        "regime_status": {k: m.get(k) for k in ("available", "pending", "reason", "fetched_at", "refreshing", "refresh_error", "as_of")},
        "top_opportunities": top,
        "major_risks": risks[:10],
        "upcoming_catalysts": cal["events"][:10],
        "catalysts_status": {k: cal.get(k) for k in ("available", "pending", "reason", "fetched_at", "refreshing", "refresh_error")},
        "portfolio": {"holdings": len(pf.holdings), "cash": pf.cash, "cash_entered": cash_entered},
        "provider_health": [h.as_dict() for h in s.health.all()],
        "readiness": ready,
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
        cash_entered = repo.get_setting(ss, "portfolio_cash") is not None
    end = last_completed_session(s.now())
    start = end - timedelta(days=260)
    series, pending = _view_bars(s, [h.ticker for h in pf.holdings] + ["SPY"], start, end)
    closes = {h.ticker: {b.day: b.close for b in series.get(h.ticker, []) if b.day <= end} for h in pf.holdings}
    bench = {b.day: b.close for b in series.get("SPY", []) if b.day <= end}
    snap = portfolio_snapshot(pf, closes, bench or None)
    s.broker_tick()
    with s.sf() as ss:
        krw = s.fx_attribution(ss, pf, snap)
    return encode(snap) | {"currency": "USD", "note": "MarketLens는 주문을 넣지 않습니다. 평가금액은 모든 종목을 같은 거래일 종가로 계산합니다.",
                           # False: the cash is the sizing assumption, not an entered amount (the account's cash counts as known)
                           "cash_entered": cash_entered or pf.cash_source != "manual", "cash_source": pf.cash_source,
                           "history_pending": pending, "unused_manual": [{"ticker": t, "quantity": q} for t, q in pf.unused_manual],
                           "broker": s.broker.view() if s.broker.enabled else None, "krw": krw}


def _view_bars(s: MarketLensService, tickers: list[str], start: date, end: date, wait: float = STORE_FIRST_WAIT) -> tuple[dict[str, list[Any]], list[str]]:
    """Daily bars for a screen. Stored history answers at once; a stretch the store lacks is fetched in the
    background (one job per name and window) — the screen waits at most ``wait`` seconds in total, then uses what is
    stored and lists the names still loading (owner report 2026-09-28: the portfolio waited on the price provider)."""
    if s.store is None:  # MOCK: generated in memory, nothing to wait for
        return {t: s.data.bars(t, start, end).value or [] for t in dict.fromkeys(tickers)}, []
    out: dict[str, list[Any]] = {}
    pending: list[str] = []
    need: dict[str, str] = {}
    for t in dict.fromkeys(tickers):
        k = f"bars:{t}:{start}:{end}"
        v = s.refresher.peek(k)
        if v.ready:
            out[t] = v.value
            continue
        stored = s.store.bars(t, start, end)
        if stored and (end - stored[-1].day).days <= 5:
            out[t] = stored  # the valuation day is stored: shown at once; an earlier gap fills in the background
            s.refresher.get(k, lambda t=t: s.data.bars(t, start, end).value or [], max_age=600.0, retry_after=120.0)
            continue
        need[t] = k
    deadline = time.monotonic() + wait
    for t, k in need.items():  # nothing current stored: the request that starts the fetch waits briefly for it
        v = s.refresher.get(k, lambda t=t: s.data.bars(t, start, end).value or [], max_age=600.0, retry_after=120.0,
                            wait=max(0.0, deadline - time.monotonic()))
        if v.ready:
            out[t] = v.value
            continue
        out[t] = s.store.bars(t, start, end)
        if v.refreshing:
            pending.append(t)
    return out, pending


# ------------------------------------------------------------------ 토스증권 account (read-only)
class TossKeyIn(BaseModel):
    client_id: str = Field(min_length=1, max_length=300)
    client_secret: str = Field(min_length=1, max_length=300)


class TossPrefsIn(BaseModel):
    cash: Literal["toss_usd", "toss_usd_krw", "manual"]


def _toss_call(fn: Any) -> dict[str, Any]:
    from marketlens.providers.live.toss import TossError

    try:
        return fn()
    except TossError as e:
        # 400 for the key / IP / account (the owner must act), 503 when Toss or the network is down
        raise HTTPException(status_code=503 if e.kind in ("UNAVAILABLE", "RATE_LIMITED") else 400, detail=e.text) from None
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None


@router.get("/broker/toss")
def toss_status(req: Request) -> dict[str, Any]:
    s = svc(req)
    s.broker_tick()
    return s.broker.view()


@router.put("/broker/toss/credentials")
def toss_connect(req: Request, body: TossKeyIn) -> dict[str, Any]:
    """Tries the key first (account, holdings, cash); only a key that works is stored — never returned or logged."""
    s = svc(req)
    _toss_call(lambda: s.broker.connect(body.client_id, body.client_secret))
    return s.broker.view()


@router.delete("/broker/toss")
def toss_disconnect(req: Request) -> dict[str, Any]:
    """Forgets the key (keychain and .env) and the account's data; entered lines and trade records are untouched."""
    s = svc(req)
    s.broker.disconnect()
    return s.broker.view()


@router.post("/broker/toss/sync")
def toss_sync(req: Request) -> dict[str, Any]:
    s = svc(req)
    _toss_call(s.broker_sync_now)
    return s.broker.view()


@router.put("/broker/toss/prefs")
def toss_prefs(req: Request, body: TossPrefsIn) -> dict[str, Any]:
    s = svc(req)
    _toss_call(lambda: s.broker.set_prefs(body.cash))
    return s.broker.view()


@router.get("/performance/backtest")
def backtest_results() -> dict[str, Any]:
    """과거 검증 (PREREGISTRATION §12): the committed summary of the measured 2016+ run (config/backtest_results.json,
    written by marketlens.backtest.summary from that run's results.json) — or that it does not exist yet."""
    import json as _json

    from marketlens.config import CONFIG_DIR

    p = CONFIG_DIR / "backtest_results.json"
    if not p.exists():
        return {"available": False, "reason": "2016년 이후 자료로 과거 검증을 계산하는 중입니다. 결과가 나오면 이 자리에 표시됩니다."}
    return _json.loads(p.read_text(encoding="utf-8"))


@router.get("/briefing")
def morning_briefing(req: Request, refresh: bool = False) -> dict[str, Any]:
    """오늘 아침 브리핑 (한국시간 07:00부터) — the overnight US session for the owner's account, in the app only."""
    return svc(req).morning_briefing(refresh=refresh)


@router.get("/broker/toss/fills")
def toss_fills(req: Request, limit: int = 300) -> dict[str, Any]:
    s = svc(req)
    return {"fills": s.broker.fills(max(1, min(limit, 2000))), "status": s.broker.status()}


@router.put("/portfolio")
def set_portfolio(req: Request, body: PortfolioIn) -> dict[str, Any]:
    s = svc(req)
    with s.sf() as ss:
        if body.cash is not None:
            repo.set_setting(ss, "portfolio_cash", str(body.cash))
        for h in body.holdings:
            repo.upsert_holding(ss, _ticker(h.ticker), h.quantity, h.cost_basis)
        ss.commit()
    s.invalidate_live_plans()  # held flags / costs of the live verdicts
    return portfolio(req)


@router.delete("/portfolio/holdings/{ticker}")
def remove_holding(req: Request, ticker: str, trades_only: bool = False, security: str | None = None) -> dict[str, Any]:
    """Removes a stock from the portfolio: its entered line and all its trade records, in one transaction
    (``trades_only``: the trade records only — "delete all records" in the ledger keeps an entered line)."""
    s = svc(req)
    out = s.remove_holding(_ticker(ticker), keep_manual=trades_only, security=security)
    s.invalidate_live_plans()
    return out


class TradeIn(BaseModel):
    ticker: str = Field(min_length=1, max_length=10)
    day: date
    kind: Literal["BUY", "SELL", "DIVIDEND", "SPLIT"]
    quantity: float = Field(default=0.0, ge=0, le=1e12, allow_inf_nan=False)
    price: float = Field(default=0.0, ge=0, le=1e9, allow_inf_nan=False)
    fees: float = Field(default=0.0, ge=0, le=1e9, allow_inf_nan=False)
    amount: float = Field(default=0.0, ge=0, le=1e12, allow_inf_nan=False)
    split_from: float = Field(default=0.0, ge=0, le=1e6, allow_inf_nan=False)
    split_to: float = Field(default=0.0, ge=0, le=1e6, allow_inf_nan=False)
    note: str = Field(default="", max_length=200)


def _ledger_view(s: MarketLensService) -> dict[str, Any]:
    with s.sf() as ss:
        groups = s.ledger(ss)
    out = []
    realized = dividends = 0.0
    for g in groups:
        pos = g["position"]
        if pos is not None:
            realized += pos.realized_pnl
            dividends += pos.dividends
        out.append({
            "security": g["security"], "ticker": g["ticker"], "last_ticker": g["last"].ticker, "error": g["error"],
            "position": None if pos is None else {"quantity": pos.quantity, "avg_cost": pos.avg_cost, "cost_basis": pos.cost_basis, "realized_pnl": pos.realized_pnl,
                                                  "dividends": pos.dividends, "fees": pos.fees, "splits_applied": list(pos.splits_applied)},
            "trades": [{"id": r.id, "ticker": r.ticker, "day": r.day.isoformat(), "kind": r.kind, "quantity": r.quantity, "price": r.price, "fees": r.fees,
                        "amount": r.amount, "split_from": r.split_from, "split_to": r.split_to, "note": r.note} for r in sorted(g["rows"], key=lambda r: (r.day, r.id))],
        })
    return {"securities": out, "realized_pnl": round(realized, 2), "dividends": round(dividends, 2),
            "note": "거래 기록이 있는 종목의 보유(수량·평단)는 거래 기록에서 계산합니다. 주식분할은 실행일 개장 전에 적용되므로 그날의 체결은 분할 후 기준으로 적으세요."}


@router.get("/transactions")
def get_transactions(req: Request) -> dict[str, Any]:
    return _ledger_view(svc(req))


@router.post("/transactions")
def add_transaction(req: Request, body: TradeIn) -> dict[str, Any]:
    """Records one trade; the client re-reads /transactions and /portfolio (the response carries only the new id)."""
    from marketlens.domain.ledger import LedgerError

    s = svc(req)
    try:
        tid = s.add_transaction(_ticker(body.ticker), body.day, body.kind, body.quantity, body.price, body.fees, body.amount, body.split_from, body.split_to, body.note)
    except LedgerError as e:
        raise HTTPException(400, str(e)) from None
    s.invalidate_live_plans()
    return {"added": tid}


@router.delete("/transactions/{tid}")
def delete_transaction(req: Request, tid: int) -> dict[str, Any]:
    from marketlens.domain.ledger import LedgerError, LedgerNotFound

    s = svc(req)
    try:
        s.delete_transaction(tid)
    except LedgerNotFound as e:
        raise HTTPException(404, str(e)) from None
    except LedgerError as e:
        raise HTTPException(400, str(e)) from None
    s.invalidate_live_plans()
    return {"deleted": tid}


@router.get("/watchlist")
def get_watchlist(req: Request) -> list[dict[str, Any]]:
    s = svc(req)
    with s.sf() as ss:
        out = []
        for w in repo.watchlist(ss):
            rec = s.latest_company_recommendation(ss, w.ticker)
            out.append({"ticker": w.ticker, "note": w.note, "added_at": w.added_at.isoformat(), "latest": _row_summary(rec, s) if rec else None})
        return out


@router.post("/watchlist/{ticker}")
def add_watch(req: Request, ticker: str) -> dict[str, str]:
    s = svc(req)
    with s.sf() as ss:
        repo.add_watch(ss, _ticker(ticker))
        ss.commit()
    s.quotes.refresh_pinned()
    s.invalidate_live_plans()  # the watched flag of the live verdicts and the quote subscription follow at once
    return {"status": "ok"}


@router.delete("/watchlist/{ticker}")
def del_watch(req: Request, ticker: str) -> dict[str, str]:
    s = svc(req)
    with s.sf() as ss:
        repo.remove_watch(ss, _ticker(ticker))
        ss.commit()
    s.quotes.refresh_pinned()
    s.invalidate_live_plans()  # the watched flag of the live verdicts and the quote subscription follow at once
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


@router.post("/sync/start")
def sync_start(req: Request) -> dict[str, Any]:
    """Start preparing the LIVE data in the background (returns at once; follow it with GET /sync/status)."""
    return svc(req).start_sync()


@router.get("/sync/status")
def sync_status(req: Request) -> dict[str, Any]:
    return svc(req).sync_status()


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
    return svc(req).readiness_view(wait_if_cached=2.0)  # the readiness screen waits briefly for a recount, then shows the last one marked


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


class SetupIn(BaseModel):
    values: dict[str, str] = Field(default_factory=dict)


@router.put("/settings/setup")
def save_setup(req: Request, body: SetupIn) -> dict[str, Any]:
    """First-run setup from the screen (no file editing). Values are stored, never returned; the running backend
    keeps its settings until it is restarted."""
    from fastapi import HTTPException

    from marketlens.config import save_setup as _save

    svc(req)  # the service must exist (and the CSRF guard has already checked the client header)
    if {"TOSS_CLIENT_ID", "TOSS_CLIENT_SECRET"} & set(body.values):
        raise HTTPException(status_code=422, detail="토스증권 키는 '토스증권 연결'에서 입력하세요 — 저장하기 전에 연결을 확인합니다")
    try:
        where = _save(body.values)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return {"saved": where, "restart_required": bool(where),
            "note": "저장했습니다. 앱을 다시 시작하면 적용됩니다. 저장한 값은 화면에 다시 표시하지 않습니다."}


@router.get("/settings")
def settings(req: Request) -> dict[str, Any]:
    s = svc(req)
    cfg = s.model_config()
    st = s.settings
    return {
        "mode": st.mode.value,
        "keys_configured": {"SEC_USER_AGENT": bool(st.sec_user_agent), "FINNHUB_API_KEY": bool(st.finnhub_api_key), "POLYGON_API_KEY": bool(st.polygon_api_key), "FRED_API_KEY": bool(st.fred_api_key), "ALPHAVANTAGE_API_KEY": bool(st.alphavantage_api_key), "FINRA_API_KEY": bool(st.finra_api_key), "ANTHROPIC_API_KEY": bool(st.anthropic_api_key), "OPENAI_API_KEY": bool(st.openai_api_key)},
        "llm_provider": st.llm_provider,
        "llm": {"provider": st.llm_provider, "available": bool(getattr(s.llm, "available", False)), "base_url_local": st.openai_base_url.startswith(("http://127.0.0.1", "http://localhost")),
                "base_url": st.openai_base_url if st.openai_base_url.startswith(("http://127.0.0.1", "http://localhost")) else None, "fast_model": st.fast_model, "deep_model": st.deep_model,
                "openai_key": bool(st.openai_api_key), "budget_usd": st.llm_budget_usd or None,
                "price_in": st.llm_price_in or None, "price_out": st.llm_price_out or None,
                "spent_usd": round(getattr(s.llm, "spent_usd", 0.0), 4) if hasattr(s.llm, "budget_usd") else None,
                "reason": getattr(s.llm, "reason", None)},
        "scheduler": {"enabled": st.scheduler, "interval_minutes": st.scan_interval_minutes, "ai_committee": st.ai_committee_on_schedule},
        "weights": dict(cfg.scoring_model.weights),
        "decision": encode(cfg.decision),
        "entry": encode(cfg.entry),
        "scanner": encode(cfg.scanner),
        "calibration": encode(cfg.calibration),
        "sector_models": [{"id": m.model_id, "name": m.name, "rationale": m.rationale, "primary_multiple": m.primary_multiple, "fundamental": [encode(r) for r in m.fundamental_rules], "valuation": [encode(r) for r in m.valuation_rules]} for m in cfg.sector_models],
        "note": "API 키 등 비밀값은 API로 절대 반환되지 않습니다. .env 파일 또는 OS 자격 증명 관리자에서 설정하세요.",
    }
