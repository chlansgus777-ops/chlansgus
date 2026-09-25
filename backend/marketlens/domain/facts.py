"""Traceable facts.

Every numeric input to MarketLens is a :class:`Fact`: a value plus where it came from, when it was
observed, when it was retrieved and its quality. Facts are frozen — nothing downstream (least of all
an LLM agent) can mutate them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime

from marketlens.domain.enums import DataMode, DataQuality

DERIVED_SOURCE = "calc"


@dataclass(frozen=True, slots=True)
class Fact:
    value: float | None
    source: str
    source_ts: datetime | None = None
    retrieved_ts: datetime | None = None
    quality: DataQuality = DataQuality.FRESH
    mode: DataMode = DataMode.LIVE
    evidence_id: str | None = None
    note: str | None = None

    @property
    def is_usable(self) -> bool:
        return self.value is not None and self.quality not in (
            DataQuality.MISSING,
            DataQuality.CONFLICTING,
        )

    def with_evidence(self, evidence_id: str) -> "Fact":
        return replace(self, evidence_id=evidence_id)

    @staticmethod
    def missing(source: str = "none", note: str | None = None, mode: DataMode = DataMode.LIVE) -> "Fact":
        return Fact(value=None, source=source, quality=DataQuality.MISSING, mode=mode, note=note)


def usable(f: Fact | None) -> float | None:
    """Return the value of a fact only if it is usable for a decision."""
    if f is None or not f.is_usable:
        return None
    return f.value


def derived(value: float | None, *inputs: Fact | None, note: str | None = None) -> Fact:
    """A deterministic calculation from other facts.

    Quality is the worst of the input qualities; a missing input makes the result missing.
    """
    order = [DataQuality.FRESH, DataQuality.DELAYED, DataQuality.STALE, DataQuality.CONFLICTING, DataQuality.MISSING]
    worst = DataQuality.FRESH
    mode = DataMode.LIVE
    ts: datetime | None = None
    for f in inputs:
        if f is None:
            worst = DataQuality.MISSING
            continue
        if order.index(f.quality) > order.index(worst):
            worst = f.quality
        if f.mode == DataMode.MOCK:
            mode = DataMode.MOCK
        if f.source_ts is not None and (ts is None or f.source_ts > ts):
            ts = f.source_ts
    if value is None:
        worst = DataQuality.MISSING
    return Fact(value=value, source=DERIVED_SOURCE, source_ts=ts, quality=worst, mode=mode, note=note)


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    """Aggregated data quality for one ticker's analysis."""

    fields: tuple[tuple[str, DataQuality], ...] = field(default_factory=tuple)
    core_missing: tuple[str, ...] = ()
    conflicts: tuple[str, ...] = ()
    stale: tuple[str, ...] = ()

    @property
    def completeness(self) -> float:
        if not self.fields:
            return 0.0
        ok = sum(1 for _, q in self.fields if q in (DataQuality.FRESH, DataQuality.DELAYED))
        return ok / len(self.fields)

    @property
    def overall(self) -> DataQuality:
        if self.core_missing:
            return DataQuality.MISSING
        if self.conflicts:
            return DataQuality.CONFLICTING
        if self.stale:
            return DataQuality.STALE
        if any(q == DataQuality.DELAYED for _, q in self.fields):
            return DataQuality.DELAYED
        return DataQuality.FRESH


def build_quality_report(facts: dict[str, Fact | None], core_fields: tuple[str, ...]) -> DataQualityReport:
    fields: list[tuple[str, DataQuality]] = []
    core_missing: list[str] = []
    conflicts: list[str] = []
    stale: list[str] = []
    for name, f in sorted(facts.items()):
        q = DataQuality.MISSING if f is None else f.quality
        if f is not None and f.value is None and q != DataQuality.CONFLICTING:
            q = DataQuality.MISSING
        fields.append((name, q))
        if q == DataQuality.CONFLICTING:
            conflicts.append(name)
        elif q == DataQuality.STALE:
            stale.append(name)
        if name in core_fields and q in (DataQuality.MISSING, DataQuality.CONFLICTING):
            core_missing.append(name)
    return DataQualityReport(
        fields=tuple(fields), core_missing=tuple(core_missing), conflicts=tuple(conflicts), stale=tuple(stale)
    )
