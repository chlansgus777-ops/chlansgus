"""Weight calibration with shadow mode and promotion gates.

- No proposal without ``min_samples`` mature outcomes.
- Per-cycle change of any weight ≤ ``max_weight_change`` × current weight (relative, default 5%).
- Total weight is preserved (zero-sum adjustment).
- A proposal first runs in SHADOW; promotion requires out-of-sample evidence.
- Segment calibration falls back to the global model when a segment is under-sampled.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from statistics import fmean
from typing import Mapping, Sequence

from marketlens.domain.evaluation import OutcomeSample, factor_ic, is_mature, non_overlapping, spearman
from marketlens.domain.scoring import COMPONENTS


@dataclass(frozen=True, slots=True)
class CalibrationConfig:
    min_samples: int = 100
    max_weight_change: float = 0.05  # relative per cycle
    horizon: int = 20
    min_ic_improvement: float = 0.01
    min_shadow_samples: int = 100
    max_hit_rate_drop: float = 0.0
    max_downside_worsening: float = 0.0
    min_segment_samples: int = 60
    max_worst_worsening: float = 0.0  # worst top-quintile outcome (drawdown proxy) may not get worse
    auto_promote: bool = False  # promotion is a human decision by default; the comparison only makes it eligible


@dataclass(frozen=True, slots=True)
class WeightProposal:
    status: str  # PROPOSED | INSUFFICIENT_SAMPLES | NO_SIGNAL
    old_weights: Mapping[str, float]
    new_weights: Mapping[str, float]
    factor_ics: Mapping[str, float | None]
    samples: int
    reason: str


def propose_weights(
    samples: Sequence[OutcomeSample],
    current: Mapping[str, float],
    as_of: date,
    cfg: CalibrationConfig | None = None,
) -> WeightProposal:
    cfg = cfg or CalibrationConfig()
    # overlapping holding periods of the same ticker are not independent → thin them first
    mature = [s for s in non_overlapping(samples, cfg.horizon) if is_mature(s.rec_day, cfg.horizon, as_of) and s.forward_returns.get(cfg.horizon) is not None]
    if len(mature) < cfg.min_samples:
        return WeightProposal("INSUFFICIENT_SAMPLES", dict(current), dict(current), {}, len(mature), f"성숙 표본 {len(mature)}개 < 최소 {cfg.min_samples}개")
    ics: dict[str, float | None] = {}
    for f in COMPONENTS:
        r = factor_ic(mature, f, cfg.horizon, as_of, min_samples=cfg.min_samples)
        ics[f] = r.ic
    valid = {f: v for f, v in ics.items() if v is not None}
    if not valid:
        return WeightProposal("NO_SIGNAL", dict(current), dict(current), ics, len(mature), "요인 IC를 계산할 수 없음")
    mean_ic = fmean(valid.values())
    # desired direction: raise factors with above-average IC, lower the others
    desired: dict[str, float] = {}
    for f, w in current.items():
        ic = valid.get(f)
        if ic is None:
            desired[f] = 0.0
            continue
        desired[f] = max(-1.0, min(1.0, (ic - mean_ic) / 0.05)) * cfg.max_weight_change * w
    pos = sum(d for d in desired.values() if d > 0)
    neg = -sum(d for d in desired.values() if d < 0)
    # zero-sum: shrink the larger side so that increases == decreases (caps remain respected)
    if pos > neg and pos > 0:
        scale = neg / pos
        desired = {f: (d * scale if d > 0 else d) for f, d in desired.items()}
    elif neg > pos and neg > 0:
        scale = pos / neg
        desired = {f: (d * scale if d < 0 else d) for f, d in desired.items()}
    new = {f: round(current[f] + desired[f], 4) for f in current}
    return WeightProposal("PROPOSED", dict(current), new, ics, len(mature), f"평균 IC {mean_ic:.4f}, 표본 {len(mature)}개")


def composite(sample: OutcomeSample, weights: Mapping[str, float]) -> float | None:
    tw = sum(weights.values())
    if tw <= 0:
        return None
    acc = 0.0
    for f, w in weights.items():
        v = sample.factors.get(f)
        if v is None:
            return None
        acc += v * w
    return acc / tw


@dataclass(frozen=True, slots=True)
class ShadowComparison:
    samples: int
    ic_production: float | None
    ic_shadow: float | None
    hit_rate_production: float | None
    hit_rate_shadow: float | None
    downside_production: float | None
    downside_shadow: float | None
    promote: bool
    reasons: tuple[str, ...]
    worst_production: float | None = None
    worst_shadow: float | None = None
    ic_halves: tuple[tuple[float | None, float | None], ...] = ()  # (production, shadow) IC per time half


def _worst_top_quintile(scored: list[tuple[date, float, float]]) -> float | None:
    by_day: dict[date, list[tuple[float, float]]] = {}
    for d, sc, r in scored:
        by_day.setdefault(d, []).append((sc, r))
    rets = [r for rows in by_day.values() if len(rows) >= 5 for _, r in sorted(rows, key=lambda x: -x[0])[: max(1, len(rows) // 5)]]
    return min(rets) if len(rets) >= 5 else None


def _top_quintile_stats(scored: list[tuple[date, float, float]]) -> tuple[float | None, float | None]:
    """Top-quintile hit rate and average downside, formed cross-sectionally *per recommendation date*."""
    by_day: dict[date, list[tuple[float, float]]] = {}
    for d, sc, r in scored:
        by_day.setdefault(d, []).append((sc, r))
    rets: list[float] = []
    for rows in by_day.values():
        if len(rows) < 5:
            continue
        top = sorted(rows, key=lambda x: -x[0])[: max(1, len(rows) // 5)]
        rets.extend(r for _, r in top)
    if len(rets) < 5:
        return None, None
    hit = sum(1 for r in rets if r > 0) / len(rets)
    downside = fmean([min(0.0, r) for r in rets])
    return hit, downside


def compare_shadow(
    samples: Sequence[OutcomeSample],
    production: Mapping[str, float],
    shadow: Mapping[str, float],
    shadow_started: date,
    as_of: date,
    cfg: CalibrationConfig | None = None,
) -> ShadowComparison:
    """Out-of-sample comparison: only recommendations made after the shadow start are used."""
    cfg = cfg or CalibrationConfig()
    oos = [
        s for s in non_overlapping(samples, cfg.horizon)
        if s.rec_day >= shadow_started and is_mature(s.rec_day, cfg.horizon, as_of) and s.forward_returns.get(cfg.horizon) is not None
    ]
    prod_pts: list[tuple[date, float, float]] = []
    sh_pts: list[tuple[date, float, float]] = []
    for s in oos:
        r = s.forward_returns[cfg.horizon]
        cp, cs = composite(s, production), composite(s, shadow)
        if r is None or cp is None or cs is None:
            continue
        prod_pts.append((s.rec_day, cp, r))
        sh_pts.append((s.rec_day, cs, r))
    n = len(prod_pts)
    ic_p = spearman([a for _, a, _ in prod_pts], [b for _, _, b in prod_pts]) if n >= 3 else None
    ic_s = spearman([a for _, a, _ in sh_pts], [b for _, _, b in sh_pts]) if n >= 3 else None
    hp, dp = _top_quintile_stats(prod_pts)
    hs, ds = _top_quintile_stats(sh_pts)
    wp, ws = _worst_top_quintile(prod_pts), _worst_top_quintile(sh_pts)
    # stability: the improvement must hold in both halves of the out-of-sample period, not just on average
    days = sorted({d for d, _, _ in prod_pts})
    halves: list[tuple[float | None, float | None]] = []
    if len(days) >= 2:
        mid = days[len(days) // 2]
        for sel in (lambda d: d < mid, lambda d: d >= mid):
            p_h = [(a, b) for d, a, b in prod_pts if sel(d)]
            s_h = [(a, b) for d, a, b in sh_pts if sel(d)]
            halves.append((spearman([a for a, _ in p_h], [b for _, b in p_h]) if len(p_h) >= 3 else None,
                           spearman([a for a, _ in s_h], [b for _, b in s_h]) if len(s_h) >= 3 else None))
    reasons: list[str] = []
    promote = True
    # fail closed: every safety metric must be measurable; "could not check" never counts as "passed"
    required = {"IC(운영)": ic_p, "IC(후보)": ic_s, "적중률(운영)": hp, "적중률(후보)": hs, "하방(운영)": dp, "하방(후보)": ds,
                "최악 결과(운영)": wp, "최악 결과(후보)": ws}
    missing = [k for k, v in required.items() if v is None]
    if missing or len(halves) < 2 or any(a is None or b is None for a, b in halves):
        promote = False
        reasons.append("필수 안전 지표를 계산할 수 없음(" + ", ".join(missing or ["기간별 안정성"]) + ") → 승격 불가, 후보 모델 유지")
    if n < cfg.min_shadow_samples:
        promote = False
        reasons.append(f"표본 외 표본 {n}개 (< {cfg.min_shadow_samples})")
    if ic_p is None or ic_s is None or ic_s - ic_p < cfg.min_ic_improvement:
        promote = False
        reasons.append("IC 개선폭이 기준 미만")
    if hp is not None and hs is not None and hs < hp - cfg.max_hit_rate_drop:
        promote = False
        reasons.append("적중률이 운영 모델보다 나쁨")
    if dp is not None and ds is not None and ds < dp - cfg.max_downside_worsening:
        promote = False
        reasons.append("하방 위험(낙폭 대용치)이 운영 모델보다 나쁨")
    if wp is not None and ws is not None and ws < wp - cfg.max_worst_worsening:
        promote = False
        reasons.append("최악 결과(낙폭 대용치)가 운영 모델보다 나쁨")
    if halves and all(a is not None and b is not None for a, b in halves) and any(b < a for a, b in halves):  # type: ignore[operator]
        promote = False
        reasons.append("기간별 안정성 부족: 한쪽 기간에서 후보 모델 IC가 운영 모델보다 낮음")
    if promote:
        reasons.append("모든 승격 조건 통과")
    return ShadowComparison(n, ic_p, ic_s, hp, hs, dp, ds, promote, tuple(reasons), wp, ws, tuple(halves))


def segment_samples(samples: Sequence[OutcomeSample], key: str, value: str, min_n: int) -> tuple[list[OutcomeSample], bool]:
    """Return (samples, is_segment). Falls back to the global set when the segment is too small."""
    seg = [s for s in samples if getattr(s, key) == value]
    if len(seg) >= min_n:
        return seg, True
    return list(samples), False


@dataclass(frozen=True, slots=True)
class SegmentReport:
    """What calibration can honestly say about one sector / regime segment.

    MarketLens scores every segment with the ONE production weight set; no segment-specific model is
    ever applied (``applied`` is always False). A segment with enough mature, non-overlapping samples gets
    its own weight *proposal* for human review; an under-sampled one says so and uses the global model."""

    key: str
    value: str
    status: str  # SEGMENT_INSUFFICIENT | SEGMENT_PROPOSAL | SEGMENT_NO_SIGNAL
    mature_samples: int
    min_samples: int
    label_ko: str
    proposal: WeightProposal | None = None
    applied: bool = False


def segment_report(samples: Sequence[OutcomeSample], key: str, value: str, current: Mapping[str, float], as_of: date, cfg: CalibrationConfig) -> SegmentReport:
    seg = [s for s in samples if getattr(s, key) == value]
    mature = [s for s in non_overlapping(seg, cfg.horizon) if is_mature(s.rec_day, cfg.horizon, as_of) and s.forward_returns.get(cfg.horizon) is not None]
    n, need = len(mature), cfg.min_segment_samples
    if n < need:
        return SegmentReport(key, value, "SEGMENT_INSUFFICIENT", n, need, f"성숙 표본 {n}개 < {need}개 → 전체 모델 사용 (전용 모델 없음)")
    prop = propose_weights(seg, current, as_of, replace(cfg, min_samples=min(cfg.min_samples, need)))
    if prop.status != "PROPOSED":
        return SegmentReport(key, value, "SEGMENT_NO_SIGNAL", n, need, f"표본 {n}개지만 {prop.reason} → 전체 모델 사용", prop)
    return SegmentReport(key, value, "SEGMENT_PROPOSAL", n, need, f"표본 {n}개로 전용 가중치 '제안'만 계산 — 검토용이며 운영 점수에는 적용하지 않음", prop)
