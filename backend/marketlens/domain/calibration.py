"""Weight calibration with shadow mode and promotion gates.

- No proposal without ``min_samples`` mature outcomes.
- Per-cycle change of any weight ≤ ``max_weight_change`` × current weight (relative, default 5%).
- Total weight is preserved (zero-sum adjustment).
- A proposal first runs in SHADOW; promotion requires out-of-sample evidence.
- Segment calibration falls back to the global model when a segment is under-sampled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from statistics import fmean
from typing import Mapping, Sequence

from marketlens.domain.evaluation import OutcomeSample, factor_ic, is_mature, spearman
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
    mature = [s for s in samples if is_mature(s.rec_day, cfg.horizon, as_of) and s.forward_returns.get(cfg.horizon) is not None]
    if len(mature) < cfg.min_samples:
        return WeightProposal("INSUFFICIENT_SAMPLES", dict(current), dict(current), {}, len(mature), f"{len(mature)} mature samples < {cfg.min_samples}")
    ics: dict[str, float | None] = {}
    for f in COMPONENTS:
        r = factor_ic(mature, f, cfg.horizon, as_of, min_samples=cfg.min_samples)
        ics[f] = r.ic
    valid = {f: v for f, v in ics.items() if v is not None}
    if not valid:
        return WeightProposal("NO_SIGNAL", dict(current), dict(current), ics, len(mature), "no factor IC could be computed")
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
    return WeightProposal("PROPOSED", dict(current), new, ics, len(mature), f"mean IC {mean_ic:.4f} over {len(mature)} samples")


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


def _top_quintile_stats(scored: list[tuple[float, float]]) -> tuple[float | None, float | None]:
    if len(scored) < 10:
        return None, None
    top = sorted(scored, key=lambda x: -x[0])[: max(2, len(scored) // 5)]
    rets = [r for _, r in top]
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
        s for s in samples
        if s.rec_day >= shadow_started and is_mature(s.rec_day, cfg.horizon, as_of) and s.forward_returns.get(cfg.horizon) is not None
    ]
    prod_pts: list[tuple[float, float]] = []
    sh_pts: list[tuple[float, float]] = []
    for s in oos:
        r = s.forward_returns[cfg.horizon]
        cp, cs = composite(s, production), composite(s, shadow)
        if r is None or cp is None or cs is None:
            continue
        prod_pts.append((cp, r))
        sh_pts.append((cs, r))
    n = len(prod_pts)
    ic_p = spearman([a for a, _ in prod_pts], [b for _, b in prod_pts]) if n >= 3 else None
    ic_s = spearman([a for a, _ in sh_pts], [b for _, b in sh_pts]) if n >= 3 else None
    hp, dp = _top_quintile_stats(prod_pts)
    hs, ds = _top_quintile_stats(sh_pts)
    reasons: list[str] = []
    promote = True
    if n < cfg.min_shadow_samples:
        promote = False
        reasons.append(f"only {n} out-of-sample samples (< {cfg.min_shadow_samples})")
    if ic_p is None or ic_s is None or ic_s - ic_p < cfg.min_ic_improvement:
        promote = False
        reasons.append("IC improvement below threshold")
    if hp is not None and hs is not None and hs < hp - cfg.max_hit_rate_drop:
        promote = False
        reasons.append("hit rate worse than production")
    if dp is not None and ds is not None and ds < dp - cfg.max_downside_worsening:
        promote = False
        reasons.append("downside (drawdown proxy) worse than production")
    if promote:
        reasons.append("all promotion gates passed")
    return ShadowComparison(n, ic_p, ic_s, hp, hs, dp, ds, promote, tuple(reasons))


def segment_samples(samples: Sequence[OutcomeSample], key: str, value: str, min_n: int) -> tuple[list[OutcomeSample], bool]:
    """Return (samples, is_segment). Falls back to the global set when the segment is too small."""
    seg = [s for s in samples if getattr(s, key) == value]
    if len(seg) >= min_n:
        return seg, True
    return list(samples), False
