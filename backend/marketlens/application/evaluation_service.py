"""Outcome tracking, paper-trading updates, IC/IR evaluation, performance summaries and calibration."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from statistics import fmean
from typing import Any

from sqlalchemy.orm import Session

from marketlens.application.codec import encode
from marketlens.domain.calibration import compare_shadow, propose_weights, segment_samples
from marketlens.domain.enums import BULLISH_ACTIONS, Action, ExitReason
from marketlens.domain.evaluation import HORIZONS, OutcomeSample, bucket_performance, factor_ic, forward_return, is_mature
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import is_trading_day, last_completed_session, next_trading_day, regular_close_time, to_ny
from marketlens.domain.paper import PaperConfig, PaperSignal, PaperTradeResult, compute_metrics, simulate
from marketlens.domain.scoring import COMPONENTS
from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import ModelVersionRow, OutcomeRow, PaperPositionRow
from marketlens.infrastructure.logging import Event, log_event

log = logging.getLogger("marketlens.evaluation")
BENCH = "SPY"


def rec_session_day(as_of: datetime) -> date:
    """Base session for forward returns: the first session that CLOSES after the recommendation.

    Using a close strictly after the recommendation time means no price move that happened before the
    recommendation can leak into its measured outcome.
    """
    local = to_ny(as_of)
    d = local.date()
    if is_trading_day(d) and local.time() < regular_close_time(d):
        return d
    return next_trading_day(d)


class EvaluationService:
    def __init__(self, svc: Any) -> None:
        self.svc = svc

    def _bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        return self.svc.data.bars(ticker, start, end).value or []

    # ------------------------------------------------------------------ outcomes
    def update_outcomes(self, as_of: datetime | None = None) -> int:
        as_of = as_of or self.svc.now()
        today = last_completed_session(as_of)
        written = 0
        with self.svc.sf() as s:
            for rec in repo.all_recommendations(s):
                if rec.mode != self.svc.mode.value:
                    continue
                have = {o.horizon for o in repo.outcomes_for(s, rec.id)}
                base_day = rec_session_day(rec.as_of)
                todo = [h for h in HORIZONS if h not in have and is_mature(base_day, h, today)]
                if not todo:
                    continue
                bars = self._bars(rec.ticker, base_day - timedelta(days=10), today)
                bench = self._bars(BENCH, base_day - timedelta(days=10), today)
                for h in todo:
                    r = forward_return(bars, base_day, h, today)
                    if r is None:
                        continue
                    b = forward_return(bench, base_day, h, today)
                    s.add(OutcomeRow(recommendation_id=rec.id, horizon=h, forward_return=r, benchmark_return=b, excess_return=(r - b) if b is not None else None, matured_on=today, recorded_at=repo.now()))
                    written += 1
            s.commit()
        log_event(log, Event.OUTCOMES_UPDATED, written=written)
        return written

    # ------------------------------------------------------------------ paper trading
    def update_paper(self, as_of: datetime | None = None) -> dict[str, int]:
        as_of = as_of or self.svc.now()
        today = last_completed_session(as_of)
        cfg: PaperConfig = self.svc.base_cfg.paper
        counts = {"opened": 0, "closed": 0, "open": 0}
        with self.svc.sf() as s:
            for pos in repo.open_paper_positions(s):
                rec = repo.get_recommendation(s, pos.recommendation_id)
                later = [r for r in repo.recommendation_history(s, pos.ticker, 200) if r.as_of > pos.recommended_at]
                events: list[tuple[date, ExitReason]] = []
                for r in sorted(later, key=lambda x: x.as_of):
                    if (r.result or {}).get("thesis_invalidated"):
                        events.append((to_ny(r.as_of).date(), ExitReason.THESIS_INVALIDATION))
                        break
                    if Action(r.final_action) not in BULLISH_ACTIONS and Action(r.final_action) != Action.HOLD:
                        events.append((to_ny(r.as_of).date(), ExitReason.RECOMMENDATION_DOWNGRADE))
                        break
                bars = self._bars(pos.ticker, to_ny(pos.recommended_at).date() - timedelta(days=5), today)
                spread_bps = None
                if rec is not None:
                    q = (rec.inputs or {}).get("quote") or {}
                    if q.get("bid") and q.get("ask") and q["ask"] >= q["bid"] > 0:
                        spread_bps = (q["ask"] - q["bid"]) / ((q["ask"] + q["bid"]) / 2) * 1e4
                sig = PaperSignal(pos.ticker, pos.recommended_at, pos.action, pos.score, pos.confidence, pos.stop, pos.target1, pos.target2, pos.thesis, pos.model_version, pos.regime, pos.sector, spread_bps)
                res = simulate(sig, bars, cfg, events, as_of=today)
                was = pos.status
                self._apply(pos, res)
                if was == "PENDING" and pos.status in ("OPEN", "CLOSED"):
                    counts["opened"] += 1
                    log_event(log, Event.PAPER_POSITION_OPENED, ticker=pos.ticker, entry=pos.entry_price)
                if pos.status == "CLOSED" and was != "CLOSED":
                    counts["closed"] += 1
                    bench = self._bars(BENCH, pos.entry_day or today, pos.closed_on or today)
                    if len(bench) >= 2:
                        pos.benchmark_return = bench[-1].close / bench[0].open - 1
                    log_event(log, Event.PAPER_POSITION_CLOSED, ticker=pos.ticker, ret=pos.return_pct, reasons=[e["reason"] for e in pos.exits])
                if pos.status == "OPEN":
                    counts["open"] += 1
            s.commit()
        return counts

    @staticmethod
    def _apply(pos: PaperPositionRow, res: PaperTradeResult) -> None:
        pos.updated_at = repo.now()
        if res.entry is None:
            if res.notes and "skipped" in res.notes[0]:
                pos.status = "SKIPPED"
            return
        pos.entry_day, pos.entry_price, pos.quantity = res.entry.day, res.entry.price, res.entry.quantity
        pos.exits = [{"day": e.day.isoformat(), "price": e.price, "quantity": e.quantity, "reason": e.reason.value if e.reason else None} for e in res.exits]
        pos.return_pct, pos.mae_pct, pos.mfe_pct, pos.holding_days = res.return_pct, res.mae_pct, res.mfe_pct, res.holding_days
        pos.status = "OPEN" if res.open else "CLOSED"
        if not res.open and res.exits:
            pos.closed_on = res.exits[-1].day

    # ------------------------------------------------------------------ evaluation
    def samples(self, s: Session) -> list[OutcomeSample]:
        out = []
        for snap, rets in repo.factor_samples(s):
            out.append(OutcomeSample(snap.ticker, snap.rec_day, {k: float(v) for k, v in snap.factors.items() if v is not None}, {h: rets.get(h) for h in HORIZONS}, snap.sector, snap.regime))
        return out

    def performance(self, period_days: int | None = None) -> dict[str, Any]:
        cfg = self.svc.base_cfg
        today = last_completed_session(self.svc.now())
        with self.svc.sf() as s:
            samples = self.samples(s)
            positions = repo.all_paper_positions(s)
            recs = {r.id: r for r in repo.all_recommendations(s)}
        if period_days:
            cutoff = today - timedelta(days=period_days)
            samples = [x for x in samples if x.rec_day >= cutoff]
            positions = [p for p in positions if to_ny(p.recommended_at).date() >= cutoff]
        factors = list(COMPONENTS) + ["issue", "total"]
        ic_table = [encode(factor_ic(samples, f, h, today, cfg.min_ic_samples, cfg.min_period_samples, cfg.min_ic_periods)) for f in factors for h in (5, 20, 60)]

        def hit(h: int, subset: list[OutcomeSample]) -> dict[str, Any]:
            rets = [x.forward_returns[h] for x in subset if x.forward_returns.get(h) is not None]
            return {"n": len(rets), "hit_rate": (sum(1 for r in rets if r > 0) / len(rets)) if rets else None, "avg_return": fmean(rets) if rets else None}

        bull_ids = {r.id for r in recs.values() if Action(r.final_action) in BULLISH_ACTIONS}
        bull_keys = {(r.ticker, rec_session_day(r.as_of)) for r in recs.values() if r.id in bull_ids}
        bullish = [x for x in samples if (x.ticker, x.rec_day) in bull_keys]
        closed = [p for p in positions if p.status == "CLOSED" and p.return_pct is not None]
        eq = [1.0]
        for p in sorted(closed, key=lambda p: p.closed_on or date.min):
            eq.append(eq[-1] * (1 + p.return_pct * 0.1))  # type: ignore[operator]
        results = [PaperTradeResult(PaperSignal(p.ticker, p.recommended_at, p.action, p.score, p.confidence, p.stop, p.target1, p.target2, "", p.model_version, p.regime, p.sector), None, (), p.status != "CLOSED", p.return_pct, p.mae_pct, p.mfe_pct, p.holding_days) for p in positions if p.status in ("OPEN", "CLOSED")]
        metrics = compute_metrics(results, [p.benchmark_return for p in closed])

        def group_perf(key: str) -> dict[str, Any]:
            groups: dict[str, list[float]] = {}
            for p in closed:
                groups.setdefault(getattr(p, key), []).append(p.return_pct or 0.0)
            return {k: {"n": len(v), "avg_return": fmean(v), "win_rate": sum(1 for x in v if x > 0) / len(v)} for k, v in groups.items()}

        return {
            "as_of": today.isoformat(),
            "samples": len(samples),
            "mature_20d": sum(1 for x in samples if x.forward_returns.get(20) is not None),
            "recommendation_hit_rate": {str(h): hit(h, bullish) for h in (1, 5, 20, 60)},
            "all_recommendations": {str(h): hit(h, samples) for h in (1, 5, 20, 60)},
            "score_buckets": {str(h): bucket_performance(samples, "total", h, today, [0, 55, 65, 72, 80, 101]) for h in (5, 20)},
            "confidence_buckets": self._conf_buckets(recs, samples),
            "factor_ic": ic_table,
            "paper": encode(metrics),
            "paper_equity_curve": eq,
            "sector_performance": group_perf("sector"),
            "regime_performance": group_perf("regime"),
            "positions": [self._pos_dict(p) for p in positions[-100:]],
        }

    @staticmethod
    def _conf_buckets(recs: dict[int, Any], samples: list[OutcomeSample]) -> list[dict[str, Any]]:
        by_key = {(r.ticker, rec_session_day(r.as_of)): r.confidence for r in recs.values()}
        out = []
        for lo, hi in ((0, 60), (60, 75), (75, 90), (90, 101)):
            rets = [x.forward_returns.get(20) for x in samples if lo <= by_key.get((x.ticker, x.rec_day), -1) < hi and x.forward_returns.get(20) is not None]
            out.append({"bucket": f"{lo}-{hi}", "n": len(rets), "avg_return_20d": fmean(rets) if rets else None})  # type: ignore[arg-type]
        return out

    @staticmethod
    def _pos_dict(p: PaperPositionRow) -> dict[str, Any]:
        return {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in p.__dict__.items() if not k.startswith("_")}

    # ------------------------------------------------------------------ calibration
    def calibrate(self) -> dict[str, Any]:
        cfg = self.svc.model_config()
        ccfg = self.svc.base_cfg.calibration
        today = last_completed_session(self.svc.now())
        with self.svc.sf() as s:
            samples = self.samples(s)
            shadow = repo.shadow_model(s)
            prod_weights = dict(cfg.scoring_model.weights)
            if shadow is not None and shadow.shadow_started is not None:
                cmp = compare_shadow(samples, prod_weights, dict(shadow.weights), shadow.shadow_started, today, ccfg)
                payload = {"comparison": encode(cmp), "production": prod_weights, "shadow": shadow.weights}
                if cmp.promote:
                    prev = repo.production_model(s)
                    if prev is not None:
                        prev.status = "RETIRED"
                    shadow.status, shadow.promoted_at = "PRODUCTION", repo.now()
                    repo.add_calibration_run(s, "PROMOTED", shadow.version, payload)
                    log_event(log, Event.MODEL_PROMOTED, version=shadow.version)
                else:
                    repo.add_calibration_run(s, "SHADOW_CONTINUES", shadow.version, payload)
                s.commit()
                log_event(log, Event.CALIBRATION_RUN, status="shadow_evaluated", promote=cmp.promote)
                return {"status": "PROMOTED" if cmp.promote else "SHADOW_CONTINUES", **payload}
            prop = propose_weights(samples, prod_weights, today, ccfg)
            segments = {}
            for key in ("sector", "regime"):
                for val in sorted({getattr(x, key) for x in samples}):
                    seg, is_seg = segment_samples(samples, key, val, ccfg.min_segment_samples)
                    segments[f"{key}:{val}"] = "segment model" if is_seg else "insufficient samples → global model"
            payload = {"proposal": encode(prop), "segments": segments}
            if prop.status != "PROPOSED":
                repo.add_calibration_run(s, prop.status, None, payload)
                s.commit()
                log_event(log, Event.CALIBRATION_RUN, status=prop.status, samples=prop.samples)
                return {"status": prop.status, **payload}
            version = f"{cfg.scoring_model.version}-cal{today.strftime('%Y%m%d')}"
            s.add(ModelVersionRow(version=version, weights=dict(prop.new_weights), status="SHADOW", parent_version=cfg.scoring_model.version, created_at=repo.now(), shadow_started=today))
            repo.add_calibration_run(s, "SHADOW_STARTED", version, payload)
            s.commit()
            log_event(log, Event.CALIBRATION_RUN, status="SHADOW_STARTED", version=version)
            return {"status": "SHADOW_STARTED", "candidate_version": version, **payload}
