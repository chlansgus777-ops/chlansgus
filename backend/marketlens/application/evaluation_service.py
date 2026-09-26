"""Outcome tracking, paper-trading account updates, IC/IR evaluation, performance summaries and calibration.

Integrity rules
- Outcomes use exact trading sessions; delisted names are valued at their last close before delisting
  (flagged), missing bars leave the outcome pending — never a neighbouring day.
- Every query is bounded by the evaluation time (no recommendation or bar from the future) and by mode.
- Repeated recommendations of the same ticker on the same session count once; hit rates report the
  number of independent samples.
- Paper results come from one simulated account (real cash, dedupe, caps, daily mark-to-market);
  they are simulations, not achievable-return guarantees.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from statistics import fmean
from typing import Any

from sqlalchemy.orm import Session

from marketlens.application.codec import encode
from marketlens.domain.corporate_actions import split_factor
from marketlens.domain.calibration import compare_shadow, propose_weights, segment_samples
from marketlens.domain.enums import BULLISH_ACTIONS, Action, ExitReason
from marketlens.domain.evaluation import HORIZONS, OutcomeSample, bucket_performance, dedupe_samples, factor_ic, forward_outcome, is_mature, rolling_ic
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import is_trading_day, last_completed_session, next_trading_day, regular_close_time, to_ny
from marketlens.domain.paper import AccountItem, PaperConfig, PaperSignal, PaperTradeResult, compute_metrics, position_notional, simulate_account
from marketlens.domain.scoring import COMPONENTS
from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import ModelVersionRow, OutcomeRow, PaperPositionRow
from marketlens.infrastructure.logging import Event, log_event

log = logging.getLogger("marketlens.evaluation")
BENCH = "SPY"
PAPER_DISCLAIMER = "모의투자 결과는 과거 데이터에 대한 시뮬레이션이며 실제 체결·수익을 보장하지 않습니다(슬리피지·호가 스프레드는 추정치)."


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


def known_at(r: Any) -> datetime:
    """When a recommendation version became known: its analysis time, or later for a version issued
    afterwards (e.g. a committee review of an earlier snapshot). Nothing acts on it before that."""
    created = getattr(r, "created_at", None)
    return max(r.as_of, created) if created is not None else r.as_of


def exit_events_for(later: list[Any], after: datetime) -> list[tuple[datetime, ExitReason]]:
    """Exit signals from later recommendations (full timestamps — fills happen at the next open)."""
    for r in sorted((x for x in later if known_at(x) > after), key=lambda x: (known_at(x), x.id)):
        if (r.result or {}).get("thesis_invalidated"):
            return [(known_at(r), ExitReason.THESIS_INVALIDATION)]
        act = Action(r.final_action)
        if act not in BULLISH_ACTIONS and act != Action.HOLD:
            return [(known_at(r), ExitReason.RECOMMENDATION_DOWNGRADE)]
    return []


class EvaluationService:
    def __init__(self, svc: Any) -> None:
        self.svc = svc

    def _bars(self, ticker: str, start: date, end: date) -> list[Bar]:
        return [b for b in (self.svc.data.bars(ticker, start, end).value or []) if b.day <= end]

    def _delisted_on(self, s: Any, ticker: str) -> date | None:
        """From the persisted security master (history), never from the current scan's universe: a name
        that disappeared from today's listing is exactly the one whose outcome must still be recorded.
        Uses the caller's session (a nested session would reset a shared SQLite connection)."""
        return repo.delisted_on(s, ticker, self.svc.mode.value)

    # ------------------------------------------------------------------ outcomes
    def update_outcomes(self, as_of: datetime | None = None) -> int:
        as_of = as_of or self.svc.now()
        today = last_completed_session(as_of)
        written = 0
        with self.svc.sf() as s:
            # one outcome per issued recommendation; later versions of the same snapshot are not new evidence
            for rec in repo.all_recommendations(s, mode=self.svc.mode.value, until=as_of, originals_only=True):
                have = {o.horizon for o in repo.outcomes_for(s, rec.id)}
                base_day = rec_session_day(rec.as_of)
                todo = [h for h in HORIZONS if h not in have and is_mature(base_day, h, today)]
                if not todo:
                    continue
                bars = self._bars(rec.ticker, base_day - timedelta(days=10), today)
                bench = self._bars(BENCH, base_day - timedelta(days=10), today)
                delisted = self._delisted_on(s, rec.ticker)
                for h in todo:
                    o = forward_outcome(bars, base_day, h, today, delisted)
                    if o.value is None:
                        continue  # stays pending (halt / data gap) — never a neighbouring day
                    b = forward_outcome(bench, base_day, h, today)
                    bv = b.value if o.status == "OK" else None
                    s.add(OutcomeRow(recommendation_id=rec.id, horizon=h, forward_return=o.value, benchmark_return=bv, excess_return=(o.value - bv) if bv is not None else None,
                                     matured_on=today, recorded_at=repo.now(), status=o.status))
                    written += 1
            s.commit()
        log_event(log, Event.OUTCOMES_UPDATED, written=written)
        return written

    # ------------------------------------------------------------------ paper trading (one account)
    def _signal(self, pos: PaperPositionRow, spread_bps: float | None, basis_date: date | None = None) -> PaperSignal:
        """The plan's price levels expressed on the share basis of the stored bars. Levels were set on the
        basis of the recommendation day; every split executed after it (and already applied to the bars,
        i.e. on/before ``basis_date``) divides them, so a 10:1 split does not turn a normal entry into a
        'gap below the stop' or a stop into an impossible level."""
        f = 1.0
        if basis_date is not None:
            f = split_factor(self.svc.data.splits(pos.ticker), to_ny(pos.recommended_at).date(), basis_date)
        return PaperSignal(pos.ticker, pos.recommended_at, pos.action, pos.score, pos.confidence, pos.stop / f, pos.target1 / f, pos.target2 / f,
                           pos.thesis, pos.model_version, pos.regime, pos.sector, spread_bps, pos.max_buy / f if pos.max_buy is not None else None)

    def update_paper(self, as_of: datetime | None = None) -> dict[str, Any]:
        """Re-simulate the whole paper account from every paper signal up to ``as_of`` (deterministic)."""
        as_of = as_of or self.svc.now()
        today = last_completed_session(as_of)
        cfg: PaperConfig = self.svc.base_cfg.paper
        with self.svc.sf() as s:
            positions = [p for p in repo.all_paper_positions(s) if p.recommended_at <= as_of]
            items: list[AccountItem] = []
            bars_by: dict[str, list[Bar]] = {}
            committee_skips: list[PaperPositionRow] = []
            for pos in positions:
                rec = repo.get_recommendation(s, pos.recommendation_id)
                if rec is not None and rec.mode != self.svc.mode.value:
                    continue
                if rec is not None and Action(rec.final_action) not in BULLISH_ACTIONS:
                    committee_skips.append(pos)  # the final (post-committee) action is no longer bullish
                    continue
                later = repo.recommendation_history(s, pos.ticker, 500, mode=self.svc.mode.value, until=as_of)
                spread_bps = None
                if rec is not None:
                    q = (rec.inputs or {}).get("quote") or {}
                    if q.get("bid") and q.get("ask") and q["ask"] >= q["bid"] > 0:
                        spread_bps = (q["ask"] - q["bid"]) / ((q["ask"] + q["bid"]) / 2) * 1e4
                items.append(AccountItem(str(pos.id), self._signal(pos, spread_bps, to_ny(as_of).date()), tuple(exit_events_for(later, pos.recommended_at))))
                if pos.ticker not in bars_by:
                    start = min(to_ny(p.recommended_at).date() for p in positions if p.ticker == pos.ticker) - timedelta(days=5)
                    bars_by[pos.ticker] = self._bars(pos.ticker, start, today)
            acct = simulate_account(items, bars_by, cfg, today)
            by_id = {str(p.id): p for p in positions}
            counts = {"opened": 0, "closed": 0, "open": 0, "skipped": 0, "pending": 0}
            for key, res in acct.trades:
                pos = by_id[key]
                was = pos.status
                self._apply(pos, res, cfg)
                if was == "PENDING" and pos.status in ("OPEN", "CLOSED"):
                    counts["opened"] += 1
                    log_event(log, Event.PAPER_POSITION_OPENED, ticker=pos.ticker, entry=pos.entry_price)
                if pos.status == "CLOSED" and was != "CLOSED":
                    counts["closed"] += 1
                    bench = self._bars(BENCH, pos.entry_day or today, pos.closed_on or today)
                    bench = [b for b in bench if pos.entry_day and pos.entry_day <= b.day <= (pos.closed_on or today)]
                    pos.benchmark_return = (bench[-1].close / bench[0].open - 1) if len(bench) >= 1 else None
                    log_event(log, Event.PAPER_POSITION_CLOSED, ticker=pos.ticker, ret=pos.return_pct, reasons=[e["reason"] for e in pos.exits])
                if pos.status == "OPEN":
                    counts["open"] += 1
            for key, why in acct.skipped:
                pos = by_id[key]
                pos.status, pos.skip_reason, pos.updated_at = "SKIPPED", why[:300], repo.now()
                pos.entry_day = pos.entry_price = pos.quantity = None
                counts["skipped"] += 1
            for key in acct.pending:
                by_id[key].status = "PENDING"
                counts["pending"] += 1
            for pos in committee_skips:
                pos.status, pos.updated_at = "SKIPPED", repo.now()
                pos.skip_reason = pos.skip_reason or "최종 추천이 AI 위원회 검토 후 매수 계열이 아님"
                pos.entry_day = pos.entry_price = pos.quantity = None
                counts["skipped"] += 1
            repo.set_setting(s, "paper_account", json.dumps(encode({
                "as_of": today, "starting_capital": cfg.starting_capital, "cash": acct.cash, "realized_pnl": acct.realized_pnl,
                "unrealized_pnl": acct.unrealized_pnl, "max_drawdown": acct.max_drawdown, "stale_marks": list(acct.stale_marks),
                "equity": [[d.isoformat(), v] for d, v in acct.equity[-750:]],
            })))
            s.commit()
        return counts

    @staticmethod
    def _apply(pos: PaperPositionRow, res: PaperTradeResult, cfg: PaperConfig) -> None:
        pos.updated_at = repo.now()
        pos.skip_reason = None
        if res.entry is None:
            return
        pos.notional = position_notional(pos.action, cfg)
        pos.entry_day, pos.entry_price, pos.quantity = res.entry.day, res.entry.price, res.entry.quantity
        pos.exits = [{"day": e.day.isoformat(), "price": e.price, "quantity": e.quantity, "reason": e.reason.value if e.reason else None} for e in res.exits]
        pos.return_pct, pos.mae_pct, pos.mfe_pct, pos.holding_days = res.return_pct, res.mae_pct, res.mfe_pct, res.holding_days
        pos.status = "OPEN" if res.open else "CLOSED"
        pos.closed_on = res.exits[-1].day if (not res.open and res.exits) else None

    def paper_account(self) -> dict[str, Any] | None:
        with self.svc.sf() as s:
            raw = repo.get_setting(s, "paper_account")
        if not raw:
            return None
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    # ------------------------------------------------------------------ evaluation
    def samples(self, s: Session, until: date | None = None) -> list[OutcomeSample]:
        out = []
        for snap, rets in repo.factor_samples(s, mode=self.svc.mode.value):
            if until is not None and snap.rec_day > until:
                continue
            out.append(OutcomeSample(snap.ticker, snap.rec_day, {k: float(v) for k, v in snap.factors.items() if v is not None}, {h: rets.get(h) for h in HORIZONS}, snap.sector, snap.regime))
        return out

    def performance(self, period_days: int | None = None) -> dict[str, Any]:
        cfg = self.svc.base_cfg
        now = self.svc.now()
        today = last_completed_session(now)
        with self.svc.sf() as s:
            samples = self.samples(s, until=today)
            positions = [p for p in repo.all_paper_positions(s) if p.recommended_at <= now]
            recs = {r.id: r for r in repo.all_recommendations(s, mode=self.svc.mode.value, until=now)}
        if period_days:
            cutoff = today - timedelta(days=period_days)
            samples = [x for x in samples if x.rec_day >= cutoff]
            positions = [p for p in positions if to_ny(p.recommended_at).date() >= cutoff]
        independent = dedupe_samples(samples)
        factors = list(COMPONENTS) + ["issue", "total"]
        ic_table = [encode(factor_ic(samples, f, h, today, cfg.min_ic_samples, cfg.min_period_samples, cfg.min_ic_periods)) for f in factors for h in (5, 20, 60)]

        def hit(h: int, subset: list[OutcomeSample]) -> dict[str, Any]:
            rets = [x.forward_returns[h] for x in subset if x.forward_returns.get(h) is not None]
            return {"n_independent": len(rets), "hit_rate": (sum(1 for r in rets if r > 0) / len(rets)) if rets else None, "avg_return": fmean(rets) if rets else None}  # type: ignore[arg-type]

        bull_keys = {(r.ticker, rec_session_day(r.as_of)) for r in recs.values() if Action(r.final_action) in BULLISH_ACTIONS}
        bullish = [x for x in independent if (x.ticker, x.rec_day) in bull_keys]
        closed = [p for p in positions if p.status == "CLOSED" and p.return_pct is not None]
        results = [PaperTradeResult(self._signal(p, None), None, (), p.status != "CLOSED", p.return_pct, p.mae_pct, p.mfe_pct, p.holding_days) for p in positions if p.status in ("OPEN", "CLOSED")]
        acct = self.paper_account()
        equity = [v for _, v in acct["equity"]] if acct and acct.get("equity") else None
        metrics = compute_metrics(results, [p.benchmark_return for p in closed], equity)

        def group_perf(key: str) -> dict[str, Any]:
            groups: dict[str, list[float]] = {}
            for p in closed:
                groups.setdefault(getattr(p, key), []).append(p.return_pct or 0.0)
            return {k: {"n": len(v), "avg_return": fmean(v), "win_rate": sum(1 for x in v if x > 0) / len(v)} for k, v in groups.items()}

        return {
            "as_of": today.isoformat(),
            "samples": len(samples),
            "independent_samples": len(independent),
            "mature_20d": sum(1 for x in independent if x.forward_returns.get(20) is not None),
            "recommendation_hit_rate": {str(h): hit(h, bullish) for h in (1, 5, 20, 60)},
            "all_recommendations": {str(h): hit(h, independent) for h in (1, 5, 20, 60)},
            "score_buckets": {str(h): bucket_performance(independent, "total", h, today, [0, 55, 65, 72, 80, 101]) for h in (5, 20)},
            "confidence_buckets": self._conf_buckets(recs, independent),
            "factor_ic": ic_table,
            "rolling_ic_total_20d": [(d.isoformat(), ic, n) for d, ic, n in rolling_ic(samples, "total", 20, today)],
            "paper": encode(metrics),
            "paper_account": acct,
            "paper_equity_curve": equity or [],
            "paper_skipped": sum(1 for p in positions if p.status == "SKIPPED"),
            "paper_disclaimer": PAPER_DISCLAIMER,
            "sector_performance": group_perf("sector"),
            "regime_performance": group_perf("regime"),
            "positions": [self._pos_dict(p) for p in positions[-100:]],
        }

    @staticmethod
    def _conf_buckets(recs: dict[int, Any], samples: list[OutcomeSample]) -> list[dict[str, Any]]:
        by_key: dict[tuple[str, date], float] = {}
        for r in sorted(recs.values(), key=lambda r: (r.as_of, r.id)):
            by_key.setdefault((r.ticker, rec_session_day(r.as_of)), r.confidence)  # first recommendation of the session
        out = []
        for lo, hi in ((0, 60), (60, 75), (75, 90), (90, 101)):
            rets = [x.forward_returns.get(20) for x in samples if lo <= by_key.get((x.ticker, x.rec_day), -1) < hi and x.forward_returns.get(20) is not None]
            out.append({"bucket": f"{lo}-{hi}", "n": len(rets), "avg_return_20d": fmean(rets) if rets else None})  # type: ignore[arg-type]
        return out

    @staticmethod
    def _pos_dict(p: PaperPositionRow) -> dict[str, Any]:
        return {k: (v.isoformat() if isinstance(v, (date, datetime)) else v) for k, v in p.__dict__.items() if not k.startswith("_")}

    # ------------------------------------------------------------------ calibration
    def promote_shadow(self) -> dict[str, Any]:
        """Human approval of a shadow model. Allowed only when the latest comparison found it eligible."""
        with self.svc.sf() as s:
            shadow = repo.shadow_model(s)
            runs = [r for r in repo.calibration_runs(s) if shadow is not None and r.candidate_version == shadow.version]
            last = runs[0] if runs else None
            if shadow is None or last is None or last.status != "PROMOTION_ELIGIBLE":
                return {"status": "REFUSED", "reason": "승격 가능 판정을 받은 후보 모델이 없습니다(최신 비교 결과 필요)."}
            prev = repo.production_model(s)
            if prev is not None:
                prev.status = "RETIRED"
            shadow.status, shadow.promoted_at = "PRODUCTION", repo.now()
            repo.add_calibration_run(s, "PROMOTED", shadow.version, {"approved_from_run": last.id})
            s.commit()
            log_event(log, Event.MODEL_PROMOTED, version=shadow.version)
            return {"status": "PROMOTED", "version": shadow.version}

    def calibrate(self) -> dict[str, Any]:
        cfg = self.svc.model_config()
        ccfg = self.svc.base_cfg.calibration
        today = last_completed_session(self.svc.now())
        with self.svc.sf() as s:
            samples = self.samples(s, until=today)
            shadow = repo.shadow_model(s)
            prod_weights = dict(cfg.scoring_model.weights)
            if shadow is not None and shadow.shadow_started is not None:
                cmp = compare_shadow(samples, prod_weights, dict(shadow.weights), shadow.shadow_started, today, ccfg)
                payload = {"comparison": encode(cmp), "production": prod_weights, "shadow": shadow.weights}
                if cmp.promote and not ccfg.auto_promote:
                    # eligible, but a production weight change is a human decision (POST /calibration/promote)
                    repo.add_calibration_run(s, "PROMOTION_ELIGIBLE", shadow.version, payload)
                    s.commit()
                    log_event(log, Event.CALIBRATION_RUN, status="promotion_eligible", promote=True)
                    return {"status": "PROMOTION_ELIGIBLE", **payload}
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
                    _seg, is_seg = segment_samples(samples, key, val, ccfg.min_segment_samples)
                    segments[f"{key}:{val}"] = "업종/국면 전용 모델" if is_seg else "표본 부족 → 전체 모델 사용"
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
