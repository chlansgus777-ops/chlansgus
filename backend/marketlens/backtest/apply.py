"""PREREGISTRATION §12 / §13 — what the 2016+ results may change in the app, decided by rules fixed before the results.

1. Data verdict from the weeks of price history: < 220 부족 (every verdict "예비"), ≥ 220 충분, ≥ 374 the weights may be
   adjusted.
2. Periods (≥ 374 weeks): the first 70 % of the analysis weeks train, the next 12 weeks are a gap (60-session returns
   never overlap), the rest is the holdout — measured once, after the proposals are fixed.
3. §12 proposal from the training verdicts (h = 60) of the verifiable components: 효과 없음 −5 points, a significantly
   negative IC (mean < 0, Holm p < 0.05) −10, never below 0; the points go to the 유효 components in proportion to their
   IC, at most +5 each; points nobody may take are dropped — the total is the weighted MEAN of the sub-scores, so this
   is the normalisation §12 names (scaling every weight back to the old sum would give the same scores).
4. §13 candidate: return_signals 유효 in training → 10 points, every other weight scaled so the sum stays the same.
5. Adoption on the holdout, each against its comparison model (§12: the operating weights; §13: the §12 result):
   (1) 60-day mean IC of the new total > 0 and ≥ the comparison's; (2) top-minus-bottom quintile at 1× costs (30 %
   delisting loss, the middle assumption) not lower; (3) the app-rule strategy's max drawdown (1×, 30 %) not worse by
   more than 2 percentage points — the strategy is re-run on the holdout with each model's own decisions.

The total of a row under other weights is recomputed from its stored component sub-scores (ScoreCard.total is the
weighted mean of the sub-scores); the comparison model recomputed this way reproduces the stored totals (checked).
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime
from typing import Any, Callable, Mapping, Sequence

from sqlalchemy import select

from marketlens.backtest.engine import UNVERIFIABLE_COMPONENTS, bt_rows, one_way_cost
from marketlens.backtest.metrics import quintiles, spearman
from marketlens.backtest.outcomes import outcome
from marketlens.domain.scoring import COMPONENTS

WEEKS_ENOUGH = 220
WEEKS_ADJUST = 374
TRAIN_SHARE = 0.70
GAP_WEEKS = 12
H = 60
VERIFIABLE = ("fundamental", "valuation", "macro", "technical", "risk", "entry_rr")
CUT_NO_EFFECT = 5.0
CUT_NEGATIVE = 10.0
MAX_GAIN = 5.0
SIGNALS_POINTS = 10.0
ADOPT_LOSS = 0.30
ADOPT_COST = 1.0
MAX_MDD_WORSE = 0.02


# ------------------------------------------------------------------------------------------------ data verdict and periods
def data_weeks(days: Sequence[date]) -> int:
    """Calendar weeks with at least one session of the benchmark's history."""
    return len({d.isocalendar()[:2] for d in days})


def data_verdict(weeks: int) -> dict[str, Any]:
    return {"weeks": weeks, "verdict": "충분" if weeks >= WEEKS_ENOUGH else "부족", "adjust_allowed": weeks >= WEEKS_ADJUST,
            "prefix": "" if weeks >= WEEKS_ENOUGH else "예비: "}


def split(times: Sequence[str]) -> tuple[list[str], list[str], list[str]]:
    ts = sorted(times)
    n_train = int(len(ts) * TRAIN_SHARE)
    return ts[:n_train], ts[n_train:n_train + GAP_WEEKS], ts[n_train + GAP_WEEKS:]


# ------------------------------------------------------------------------------------------------ §12 and §13 proposals
def adjust_weights(weights: Mapping[str, float], train: Mapping[str, Any]) -> tuple[dict[str, float], list[str]]:
    """§12 on the operating ``weights`` from the training elements at h = 60 (``train[f"component.{c}"]``)."""
    w = {k: float(v) for k, v in weights.items()}
    log: list[str] = []
    freed = 0.0
    valid: dict[str, float] = {}
    for c in VERIFIABLE:
        e = train.get(f"component.{c}") or {}
        verdict, ic, p = str(e.get("verdict") or ""), e.get("mean_ic"), e.get("holm_p")
        cut = 0.0
        if ic is not None and ic < 0 and p is not None and p < 0.05:
            cut = CUT_NEGATIVE
        elif verdict.endswith("효과 없음"):
            cut = CUT_NO_EFFECT
        if cut:
            take = min(cut, w[c])
            w[c] -= take
            freed += take
            log.append(f"{c}: {verdict or '판정 없음'} (IC {ic:+.4f}) → −{take:g}점" if ic is not None else f"{c}: −{take:g}점")
        elif verdict.endswith("유효") and ic is not None and ic > 0:
            valid[c] = ic
    left = freed
    if freed and valid:
        tot = sum(valid.values())
        for c, ic in sorted(valid.items()):
            gain = min(MAX_GAIN, freed * ic / tot)
            w[c] += gain
            left -= gain
            log.append(f"{c}: 유효 (IC {ic:+.4f}) → +{gain:.2f}점")
    if left > 1e-9:
        # nobody (or not everybody, the +5 cap) could take them: the total is normalised — the score is the weighted
        # mean of the sub-scores (0–100 whatever the sum), so giving them back pro rata to every component would score
        # exactly the same; the unverifiable weights stay as they are
        log.append(f"남은 {left:.2f}점은 나눠 줄 유효 요소가 없어 합계 정규화(점수는 가중 평균이라 0–100 유지)")
    return {k: round(v, 4) for k, v in w.items()}, log


def signals_candidate(weights: Mapping[str, float], train: Mapping[str, Any]) -> tuple[dict[str, float] | None, str]:
    e = train.get("component.return_signals") or {}
    verdict = str(e.get("verdict") or "측정 불가")
    if not verdict.endswith("유효"):
        return None, f"학습 기간 판정 {verdict} → 0점 유지, 채택 시험 안 함"
    total = sum(weights.values())
    rest = total - weights.get("return_signals", 0.0)
    scale = (total - SIGNALS_POINTS) / rest if rest > 0 else 0.0
    w = {k: round((v * scale if k != "return_signals" else SIGNALS_POINTS), 4) for k, v in weights.items()}
    return w, f"학습 기간 판정 {verdict} → 후보 {SIGNALS_POINTS:g}점(나머지 요소 ×{scale:.4f})"


# ------------------------------------------------------------------------------------------------ holdout measurement
def backtest_weights(w: Mapping[str, float]) -> dict[str, float]:
    """The weights a backtest scores with: the unverifiable components at 0 (engine.backtest_config)."""
    return {k: (0.0 if k in UNVERIFIABLE_COMPONENTS else float(v)) for k, v in w.items()}


def rescore(p: Mapping[str, Any], weights: Mapping[str, float]) -> float:
    """ScoreCard.total of a stored row under other weights (a component the row predates scores 0 weight-free)."""
    comps = p["components"]
    tw = sum(weights.get(c, 0.0) for c in COMPONENTS if c in comps)
    if tw <= 0:
        return 0.0
    return round(sum(comps[c]["sub"] * weights.get(c, 0.0) for c in COMPONENTS if c in comps) / tw * 100, 2)


def holdout_cross_sections(results: Any, data: Any, times: set[str], min_names: int) -> list[list[dict[str, Any]]]:
    data_end = data.last_session
    with results.connect() as c:
        rows = [dict(r._mapping) for r in c.execute(select(bt_rows).where(bt_rows.c.eligible == 1).order_by(bt_rows.c.t, bt_rows.c.key))]
    by_t: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["t"] in times:
            by_t.setdefault(r["t"], []).append(r)
    out = []
    for t, rs in sorted(by_t.items()):
        t_day = datetime.fromisoformat(t).date()
        cs = []
        for r in rs:
            ln = data.by_key.get(r["key"])
            o = outcome(ln, t_day, H, data_end) if ln else None
            if o is None:
                continue
            p = json.loads(r["payload"])
            cs.append({"p": p, "ret": o.ret, "adv": p.get("adv20")})
        if len(cs) >= min_names:
            out.append(cs)
    return out


def score_quality(weeks: Sequence[Sequence[dict[str, Any]]], weights: Mapping[str, float]) -> dict[str, Any]:
    """60-day mean rank IC of the total under ``weights`` and the top-minus-bottom quintile (1× cost, 30 % loss)."""
    ics, tmb = [], []
    for cs in weeks:
        tot = [rescore(x["p"], weights) for x in cs]
        v = spearman(tot, [x["ret"][0.0] for x in cs])
        if v is not None:
            ics.append(v)
        net = [x["ret"][ADOPT_LOSS] - 2 * one_way_cost(x["adv"], ADOPT_COST) for x in cs]
        qs = quintiles(tot)
        top = [n for n, q in zip(net, qs) if q == 5]
        bot = [n for n, q in zip(net, qs) if q == 1]
        if top and bot:
            tmb.append(sum(top) / len(top) - sum(bot) / len(bot))
    return {"mean_ic": (sum(ics) / len(ics)) if ics else None, "weeks": len(ics), "top_minus_bottom": (sum(tmb) / len(tmb)) if tmb else None}


def stored_totals_reproduced(weeks: Sequence[Sequence[dict[str, Any]]], weights: Mapping[str, float]) -> bool:
    return all(abs(rescore(x["p"], weights) - x["p"]["total"]) <= 0.011 for cs in weeks for x in cs)


def adopt(new: Mapping[str, Any], old: Mapping[str, Any], new_mdd: float | None, old_mdd: float | None) -> dict[str, Any]:
    c1 = new["mean_ic"] is not None and old["mean_ic"] is not None and new["mean_ic"] > 0 and new["mean_ic"] >= old["mean_ic"]
    c2 = new["top_minus_bottom"] is not None and old["top_minus_bottom"] is not None and new["top_minus_bottom"] >= old["top_minus_bottom"]
    c3 = new_mdd is not None and old_mdd is not None and new_mdd >= old_mdd - MAX_MDD_WORSE
    return {"ic": c1, "quintile": c2, "drawdown": c3, "adopted": bool(c1 and c2 and c3),
            "new": dict(new) | {"max_drawdown": new_mdd}, "old": dict(old) | {"max_drawdown": old_mdd}}


# ------------------------------------------------------------------------------------------------ the whole §12/§13 step
def apply_rules(weights: Mapping[str, float], verdict: Mapping[str, Any], train_h60: Mapping[str, Any] | None,
                holdout_weeks: Sequence[Sequence[dict[str, Any]]], strategy_mdd: Callable[[Mapping[str, float]], float | None]) -> dict[str, Any]:
    """``strategy_mdd(weights)``: max drawdown (1×, 30 %) of the app-rule strategy re-run on the holdout with ``weights``
    (operating-style weights; the backtest zeroes the unverifiable ones itself)."""
    op = {k: float(v) for k, v in weights.items()}
    out: dict[str, Any] = {"data": dict(verdict), "operating_weights": op}
    if not verdict["adjust_allowed"] or train_h60 is None:
        out["s12"] = {"proposed": None, "adopted": False, "why": f"자료 {verdict['weeks']}주 < {WEEKS_ADJUST}주 — 조정하지 않음"}
        out["s13"] = {"candidate": None, "adopted": False, "why": "기간 나누기 불가(자료 부족) — 채택 시험 안 함"}
        out["final_weights"] = op
        return out
    ok_old = stored_totals_reproduced(holdout_weeks, backtest_weights(op))
    out["stored_totals_reproduced"] = ok_old
    if not ok_old:
        raise RuntimeError("the stored totals are not reproduced from the sub-scores: the rescoring would compare something else")
    mdd_cache: dict[str, float | None] = {}

    def mdd(w: Mapping[str, float]) -> float | None:
        k = json.dumps(sorted(backtest_weights(w).items()))
        if k not in mdd_cache:
            mdd_cache[k] = strategy_mdd(w)
        return mdd_cache[k]

    proposed, log = adjust_weights(op, train_h60)
    s12: dict[str, Any] = {"proposed": proposed, "log": log}
    if backtest_weights(proposed) == backtest_weights(op):
        s12.update(adopted=False, why="학습 기간 판정으로 바뀌는 가중치 없음")
        after12 = op
    else:
        res = adopt(score_quality(holdout_weeks, backtest_weights(proposed)), score_quality(holdout_weeks, backtest_weights(op)), mdd(proposed), mdd(op))
        s12.update(res)
        s12["why"] = "검증 기간 조건 3개 모두 만족 → 채택" if res["adopted"] else "검증 기간 조건 불만족 → 가중치 그대로"
        after12 = proposed if res["adopted"] else op
    out["s12"] = s12
    cand, why = signals_candidate(after12, train_h60)
    s13: dict[str, Any] = {"candidate": cand, "why": why, "train": {k: (train_h60.get("component.return_signals") or {}).get(k) for k in ("verdict", "mean_ic", "holm_p", "criteria")}}
    final = after12
    if cand is None:
        s13["adopted"] = False
    else:
        res = adopt(score_quality(holdout_weeks, backtest_weights(cand)), score_quality(holdout_weeks, backtest_weights(after12)), mdd(cand), mdd(after12))
        s13.update(res)
        s13["why"] = why + (" · 검증 기간 조건 3개 모두 만족 → 채택" if res["adopted"] else " · 검증 기간 조건 불만족 → 0점 유지")
        if res["adopted"]:
            final = cand
    out["s13"] = s13
    out["final_weights"] = final
    out["changed"] = final != op
    return _finite(out)


def _finite(x: Any) -> Any:
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else round(x, 8)
    if isinstance(x, dict):
        return {k: _finite(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_finite(v) for v in x]
    return x
