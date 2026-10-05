"""Traceable facts.

Every numeric input to MarketLens is a :class:`Fact`: a value plus where it came from, when it was
observed, when it was retrieved and its quality. Facts are frozen — nothing downstream (least of all
an LLM agent) can mutate them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Mapping

from marketlens.domain.enums import DataMode, DataQuality
from marketlens.domain.freshness import FreshnessCheck

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
    published_ts: datetime | None = None  # when the value became public (filing / release), if known

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
    checks: tuple[FreshnessCheck, ...] = ()  # per-type freshness details (age, limits, Korean reason)

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


def build_quality_report(facts: dict[str, Fact | None], core_fields: tuple[str, ...], checks: tuple[FreshnessCheck, ...] = ()) -> DataQualityReport:
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
        fields=tuple(fields), core_missing=tuple(core_missing), conflicts=tuple(conflicts), stale=tuple(stale), checks=checks
    )


EXECUTION_FIELDS = ("price", "price_history", "fundamentals")  # == pipeline.CORE_FIELDS


def execution_quality(report: Any, recorded: str | None) -> str:
    """The data quality an analysis is *acted on* with: its core fields only (price, price history, financials).
    ``recorded`` (the overall quality) is STALE / CONFLICTING when any field is — a news headline or an estimate two
    sources disagree on made a minute-old BUY read "만료" (owner 2026-10-05, MU: 매수 · 만료 · 충돌). The decision itself
    already vetoes a stale, missing or seriously conflicting core field (DATA INSUFFICIENT), and weighs the rest.
    Without the stored per-field report the recorded overall quality is used unchanged."""
    if isinstance(report, DataQualityReport):
        fields, core_missing = list(report.fields), list(report.core_missing)
    elif isinstance(report, Mapping) and isinstance(report.get("fields"), list):
        fields, core_missing = [tuple(f) for f in report["fields"]], list(report.get("core_missing") or [])
    else:
        return recorded or DataQuality.MISSING.value
    q = {str(n): (v.value if isinstance(v, DataQuality) else str(v)) for n, v in fields}
    core = [q.get(n, DataQuality.MISSING.value) for n in EXECUTION_FIELDS]
    if core_missing or DataQuality.MISSING.value in core:
        return DataQuality.MISSING.value
    for level in (DataQuality.CONFLICTING.value, DataQuality.STALE.value, DataQuality.DELAYED.value):
        if level in core:
            return level
    return DataQuality.FRESH.value
