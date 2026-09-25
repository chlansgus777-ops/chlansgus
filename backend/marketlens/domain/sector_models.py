"""Sector-specific fundamental models.

NVDA and JPM must never be evaluated with the same formula. Each :class:`SectorModel` declares which
metrics matter and what "bad" and "good" look like for that business model. Definitions are data
(loaded from ``config/sector_models.toml`` by the application layer) and versioned.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from marketlens.domain.market import Security


@dataclass(frozen=True, slots=True)
class MetricRule:
    metric: str
    label: str
    weight: float
    bad: float
    good: float

    def subscore(self, value: float) -> float:
        """Linear map bad→0, good→1 (works for both directions), clipped to [0, 1]."""
        if self.good == self.bad:
            return 1.0 if value >= self.good else 0.0
        t = (value - self.bad) / (self.good - self.bad)
        return max(0.0, min(1.0, t))


@dataclass(frozen=True, slots=True)
class SectorModel:
    model_id: str
    name: str
    sectors: tuple[str, ...]
    industry_keywords: tuple[str, ...]
    fundamental_rules: tuple[MetricRule, ...]
    valuation_rules: tuple[MetricRule, ...]
    primary_multiple: str
    rationale: str
    min_coverage: float = 0.5
    ticker_overrides: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RuleItem:
    metric: str
    label: str
    value: float | None
    subscore: float | None
    weight: float


@dataclass(frozen=True, slots=True)
class RuleScore:
    subscore: float | None  # 0..1 (None when coverage below the model minimum)
    coverage: float  # share of rule weight with available data
    items: tuple[RuleItem, ...]

    @property
    def missing(self) -> tuple[str, ...]:
        return tuple(i.metric for i in self.items if i.value is None)


def score_rules(rules: Sequence[MetricRule], metrics: Mapping[str, float | None], min_coverage: float) -> RuleScore:
    total_w = sum(r.weight for r in rules)
    items: list[RuleItem] = []
    acc = 0.0
    w_avail = 0.0
    for r in rules:
        v = metrics.get(r.metric)
        if v is None:
            items.append(RuleItem(r.metric, r.label, None, None, r.weight))
            continue
        s = r.subscore(float(v))
        items.append(RuleItem(r.metric, r.label, float(v), round(s, 4), r.weight))
        acc += s * r.weight
        w_avail += r.weight
    coverage = w_avail / total_w if total_w else 0.0
    sub = acc / w_avail if w_avail > 0 and coverage >= min_coverage else None
    return RuleScore(subscore=round(sub, 4) if sub is not None else None, coverage=round(coverage, 4), items=tuple(items))


def select_sector_model(sec: Security, models: Sequence[SectorModel], fallback_id: str = "generic") -> tuple[SectorModel, str]:
    """Pick the sector model and explain why (shown in the UI)."""
    t = sec.ticker.upper()
    for m in models:
        if t in m.ticker_overrides:
            return m, f"{sec.ticker} explicitly mapped to the {m.name} model"
    industry = (sec.industry or "").lower()
    for m in models:
        for kw in m.industry_keywords:
            if kw.lower() in industry:
                return m, f"industry '{sec.industry}' matches keyword '{kw}' → {m.name} model ({m.rationale})"
    for m in models:
        if sec.sector in m.sectors:
            return m, f"sector '{sec.sector}' → {m.name} model ({m.rationale})"
    fb = next((m for m in models if m.model_id == fallback_id), None)
    if fb is None:
        raise ValueError("sector model configuration has no generic fallback")
    return fb, f"no specific model for sector '{sec.sector}' / industry '{sec.industry}' → generic model"
