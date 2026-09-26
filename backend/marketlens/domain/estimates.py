"""Consensus estimates from free sources, and revisions MarketLens accumulates itself.

Two kinds of revision numbers exist and are never mixed silently:
- PROVIDER: the provider itself reports the consensus N days ago (e.g. Alpha Vantage 7/30/60/90).
- SELF: MarketLens stored a snapshot N days ago (append-only history, e.g. the Finnhub earnings calendar).
  Windows longer than the stored history are ACCUMULATING ("42/90일"), and days before the first stored
  snapshot are UNKNOWN — nothing is back-filled, interpolated or inferred from prices.

Values are only compared for the SAME fiscal period (a consensus for a later period is not a revision).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Mapping, Sequence

WINDOWS = (7, 30, 60, 90)
SNAPSHOT_TOLERANCE_DAYS = 4  # a snapshot up to 4 days older than the window target still measures it


@dataclass(frozen=True, slots=True)
class EstimateObservation:
    ticker: str
    provider: str
    period: str  # "FY2027", "FQ2026Q4", or the provider's period end "2026-12-31"
    period_type: str  # quarter | annual
    period_end: date | None
    observed_on: date
    eps: float | None = None
    revenue: float | None = None
    analyst_count: int | None = None
    eps_high: float | None = None
    eps_low: float | None = None
    horizon: str = ""  # provider label, e.g. "current fiscal year", "next fiscal quarter"
    report_date: date | None = None  # for calendar rows: when the period will be reported
    provider_revisions: Mapping[str, float | None] = field(default_factory=dict)  # eps_7d_ago, eps_30d_ago, up_30d, ...
    provider_timestamp: datetime | None = None


@dataclass(frozen=True, slots=True)
class RevisionValue:
    window: int
    value: float | None  # fractional change of the consensus over the window
    status: str  # READY | ACCUMULATING | UNKNOWN
    basis: str  # PROVIDER | SELF | NONE
    detail: str  # e.g. "자체 누적 42/90일"


def _pct(new: float | None, old: float | None) -> float | None:
    if new is None or old is None or old == 0:
        return None
    return (new - old) / abs(old)


def self_revision(history: Sequence[EstimateObservation], metric: str, as_of: date, window: int) -> RevisionValue:
    """Revision of one provider's consensus for ONE period from MarketLens's own stored snapshots."""
    rows = sorted((h for h in history if h.observed_on <= as_of and getattr(h, metric) is not None), key=lambda h: h.observed_on)
    if not rows:
        return RevisionValue(window, None, "UNKNOWN", "NONE", "저장된 추정치 스냅샷 없음")
    cur = rows[-1]
    if (as_of - cur.observed_on).days > SNAPSHOT_TOLERANCE_DAYS:
        return RevisionValue(window, None, "UNKNOWN", "NONE", f"최근 스냅샷이 {(as_of - cur.observed_on).days}일 전 — 현재 추정치 불명")
    target = date.fromordinal(as_of.toordinal() - window)
    past = [h for h in rows if h.observed_on <= target]
    if not past:
        have = (as_of - rows[0].observed_on).days
        return RevisionValue(window, None, "ACCUMULATING", "SELF", f"자체 누적 {have}/{window}일")
    old = past[-1]
    if (target - old.observed_on).days > SNAPSHOT_TOLERANCE_DAYS:
        return RevisionValue(window, None, "UNKNOWN", "SELF", f"{window}일 전 무렵 스냅샷 공백")
    return RevisionValue(window, _pct(getattr(cur, metric), getattr(old, metric)), "READY", "SELF", f"자체 누적 스냅샷 {old.observed_on.isoformat()} → {cur.observed_on.isoformat()}")


def provider_revision(obs: EstimateObservation, window: int) -> RevisionValue:
    old = obs.provider_revisions.get(f"eps_{window}d_ago")
    if old is None:
        return RevisionValue(window, None, "UNKNOWN", "NONE", f"{obs.provider}: {window}일 전 값 미제공")
    return RevisionValue(window, _pct(obs.eps, old), "READY", "PROVIDER", f"{obs.provider} 제공 {window}일 전 컨센서스")


@dataclass(frozen=True, slots=True)
class CrossCheck:
    status: str  # CONSISTENT | DATA_CONFLICT | SEVERE_DATA_CONFLICT | SINGLE_SOURCE
    detail: str
    diff: float | None = None


def cross_check(a: EstimateObservation | None, b: EstimateObservation | None, warn: float = 0.03, severe: float = 0.10) -> CrossCheck:
    """Compare two providers' EPS consensus for the SAME period. Never averaged."""
    if a is None or b is None or a.eps is None or b.eps is None:
        return CrossCheck("SINGLE_SOURCE", "비교할 두 번째 공급자 값 없음")
    d = abs(a.eps - b.eps) / max(abs(a.eps), abs(b.eps), 1e-9)
    txt = f"{a.provider} {a.eps:g} vs {b.provider} {b.eps:g} (차이 {d:.1%})"
    if d <= warn:
        return CrossCheck("CONSISTENT", txt, d)
    return CrossCheck("DATA_CONFLICT" if d <= severe else "SEVERE_DATA_CONFLICT", txt, d)


def same_quarter(av_quarter: EstimateObservation, calendar_row: EstimateObservation) -> bool:
    """A provider quarter (period end P) and a calendar row (report date R) describe the same quarter when
    R falls 10–100 days after P (earnings are reported after the quarter closes)."""
    if av_quarter.period_end is None or calendar_row.report_date is None:
        return False
    lag = (calendar_row.report_date - av_quarter.period_end).days
    return 10 <= lag <= 100


def ntm_eps(fy1: EstimateObservation | None, fy2: EstimateObservation | None, as_of: date) -> tuple[float | None, str]:
    """Next-twelve-months EPS: FY1 and FY2 weighted by the months left in FY1 (standard NTM blend).
    Only FY1 known → FY1 (labelled); nothing known → None."""
    if fy1 is None or fy1.eps is None:
        return None, "없음"
    if fy2 is None or fy2.eps is None or fy1.period_end is None:
        return fy1.eps, "FY1"
    left = max(0.0, min(1.0, (fy1.period_end - as_of).days / 365.0))
    return left * fy1.eps + (1 - left) * fy2.eps, f"NTM(FY1 {left:.0%} + FY2 {1 - left:.0%})"
