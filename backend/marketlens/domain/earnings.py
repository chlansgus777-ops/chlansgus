"""Earnings intelligence and analyst revisions.

Key distinction: a *good* number (absolute growth) is not the same as a *better-than-expected* number
(surprise vs consensus). Both are computed and kept separate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Sequence

from marketlens.domain.enums import StrEnum
from marketlens.domain.market_calendar import to_ny


class ExpectationBar(StrEnum):
    LOW = "LOW"
    NORMAL = "NORMAL"
    HIGH = "HIGH"
    UNKNOWN = "UNKNOWN"


class ResultQuality(StrEnum):
    BEAT_AND_RAISE = "BEAT_AND_RAISE"
    BEAT = "BEAT"
    BEAT_WEAK_GUIDE = "BEAT_WEAK_GUIDE"
    GUIDE_UP = "GUIDE_UP"
    INLINE = "INLINE"
    GUIDE_DOWN = "GUIDE_DOWN"
    MISS_STRONG_GUIDE = "MISS_STRONG_GUIDE"
    MISS = "MISS"
    MISS_AND_LOWER = "MISS_AND_LOWER"
    UNKNOWN = "UNKNOWN"


# (beat, guide) -> quality; None = in line / not provided
_QUALITY_TABLE: dict[tuple[bool | None, bool | None], ResultQuality] = {
    (True, True): ResultQuality.BEAT_AND_RAISE,
    (True, None): ResultQuality.BEAT,
    (True, False): ResultQuality.BEAT_WEAK_GUIDE,
    (None, True): ResultQuality.GUIDE_UP,
    (None, None): ResultQuality.INLINE,
    (None, False): ResultQuality.GUIDE_DOWN,
    (False, True): ResultQuality.MISS_STRONG_GUIDE,
    (False, None): ResultQuality.MISS,
    (False, False): ResultQuality.MISS_AND_LOWER,
}

RESULT_KO: dict[ResultQuality, str] = {
    ResultQuality.BEAT_AND_RAISE: "예상 상회 + 가이던스 상향",
    ResultQuality.BEAT: "예상 상회",
    ResultQuality.BEAT_WEAK_GUIDE: "예상 상회했으나 가이던스 부진",
    ResultQuality.GUIDE_UP: "예상 부합 + 가이던스 상향",
    ResultQuality.INLINE: "예상 부합",
    ResultQuality.GUIDE_DOWN: "예상 부합했으나 가이던스 하향",
    ResultQuality.MISS_STRONG_GUIDE: "예상 하회했으나 가이던스 양호",
    ResultQuality.MISS: "예상 하회",
    ResultQuality.MISS_AND_LOWER: "예상 하회 + 가이던스 하향",
    ResultQuality.UNKNOWN: "판단 불가",
}

QUALITY_SCORE: dict[ResultQuality, float] = {
    ResultQuality.BEAT_AND_RAISE: 1.0,
    ResultQuality.BEAT: 0.75,
    ResultQuality.GUIDE_UP: 0.7,
    ResultQuality.MISS_STRONG_GUIDE: 0.55,
    ResultQuality.INLINE: 0.5,
    ResultQuality.BEAT_WEAK_GUIDE: 0.4,
    ResultQuality.GUIDE_DOWN: 0.3,
    ResultQuality.MISS: 0.25,
    ResultQuality.MISS_AND_LOWER: 0.0,
}


@dataclass(frozen=True, slots=True)
class Guidance:
    next_q_revenue_low: float | None = None
    next_q_revenue_high: float | None = None
    next_q_revenue_consensus: float | None = None
    next_q_eps_low: float | None = None
    next_q_eps_high: float | None = None
    next_q_eps_consensus: float | None = None
    fy_revenue_mid: float | None = None
    fy_revenue_consensus: float | None = None
    fy_eps_mid: float | None = None
    fy_eps_consensus: float | None = None
    gross_margin_guide: float | None = None
    source: str | None = None  # e.g. SEC 8-K Exhibit 99 URL
    evidence: str | None = None  # the exact sentence(s) the numbers were read from
    confidence: str | None = None  # MEDIUM | LOW (rule-extracted)


@dataclass(frozen=True, slots=True)
class EarningsReport:
    report_date: date
    fiscal_label: str
    source: str
    revenue_actual: float | None = None
    revenue_consensus: float | None = None
    eps_actual: float | None = None
    eps_consensus: float | None = None
    gross_margin: float | None = None
    operating_margin: float | None = None
    fcf: float | None = None
    guidance: Guidance = field(default_factory=Guidance)
    # qualitative fields come from transcripts/filings; if AI-classified they are flagged
    management_tone: str | None = None  # "positive" | "neutral" | "negative"
    management_tone_source: str | None = None
    call_highlights: tuple[str, ...] = ()
    pre_earnings_run_pct: float | None = None  # 20d return into the print
    price_reaction_pct: float | None = None  # next-session reaction


def _surprise(actual: float | None, cons: float | None) -> float | None:
    if actual is None or cons is None or cons == 0:
        return None
    return (actual - cons) / abs(cons)


def _mid(lo: float | None, hi: float | None) -> float | None:
    if lo is None or hi is None:
        return lo if hi is None else hi
    return (lo + hi) / 2


@dataclass(frozen=True, slots=True)
class EarningsAssessment:
    revenue_surprise: float | None
    eps_surprise: float | None
    guide_rev_vs_cons: float | None
    guide_eps_vs_cons: float | None
    fy_guide_rev_vs_cons: float | None
    result_quality: ResultQuality
    expectation_bar: ExpectationBar
    beat_streak: int
    notes: tuple[str, ...]


def assess_earnings(
    reports: list[EarningsReport],
    inline_band: float = 0.01,
    high_bar_run: float = 0.15,
    low_bar_run: float = -0.10,
) -> EarningsAssessment | None:
    if not reports:
        return None
    rs = sorted(reports, key=lambda r: r.report_date)
    last = rs[-1]
    rev_s = _surprise(last.revenue_actual, last.revenue_consensus)
    eps_s = _surprise(last.eps_actual, last.eps_consensus)
    g = last.guidance
    guide_rev = _surprise(_mid(g.next_q_revenue_low, g.next_q_revenue_high), g.next_q_revenue_consensus)
    guide_eps = _surprise(_mid(g.next_q_eps_low, g.next_q_eps_high), g.next_q_eps_consensus)
    fy_rev = _surprise(g.fy_revenue_mid, g.fy_revenue_consensus)

    notes: list[str] = []
    beat = None
    if rev_s is not None or eps_s is not None:
        vals = [v for v in (rev_s, eps_s) if v is not None]
        if all(v > inline_band for v in vals):
            beat = True
        elif all(v < -inline_band for v in vals):
            beat = False
    guide_vals = [v for v in (guide_rev, guide_eps, fy_rev) if v is not None]
    guide = None
    if guide_vals:
        avg = sum(guide_vals) / len(guide_vals)
        guide = True if avg > inline_band else False if avg < -inline_band else None

    if rev_s is None and eps_s is None and not guide_vals:
        rq = ResultQuality.UNKNOWN
    else:
        rq = _QUALITY_TABLE[(beat, guide)]
    if rq == ResultQuality.BEAT_WEAK_GUIDE:
        notes.append("실적은 예상을 넘었지만 가이던스가 컨센서스 아래: 질 낮은 서프라이즈")
    if rq == ResultQuality.MISS_STRONG_GUIDE:
        notes.append("분기 실적은 하회했지만 가이던스는 컨센서스 위")

    streak = 0
    for r in reversed(rs):
        s = _surprise(r.eps_actual, r.eps_consensus)
        if s is not None and s > 0:
            streak += 1
        else:
            break

    bar = ExpectationBar.UNKNOWN
    if last.pre_earnings_run_pct is not None:
        if last.pre_earnings_run_pct >= high_bar_run or streak >= 6:
            bar = ExpectationBar.HIGH
        elif last.pre_earnings_run_pct <= low_bar_run:
            bar = ExpectationBar.LOW
        else:
            bar = ExpectationBar.NORMAL
    if bar == ExpectationBar.HIGH:
        notes.append("시장 기대치가 높음: 평범한 서프라이즈로는 부족할 수 있음")
    return EarningsAssessment(
        revenue_surprise=rev_s,
        eps_surprise=eps_s,
        guide_rev_vs_cons=guide_rev,
        guide_eps_vs_cons=guide_eps,
        fy_guide_rev_vs_cons=fy_rev,
        result_quality=rq,
        expectation_bar=bar,
        beat_streak=streak,
        notes=tuple(notes),
    )


@dataclass(frozen=True, slots=True)
class AnalystSnapshot:
    as_of: date
    source: str
    forward_eps: float | None = None
    forward_revenue: float | None = None
    eps_revision_7d: float | None = None  # fractional change of FY1 EPS consensus
    eps_revision_30d: float | None = None
    eps_revision_90d: float | None = None
    revenue_revision_30d: float | None = None
    revenue_revision_90d: float | None = None
    analyst_count: int | None = None
    estimate_dispersion: float | None = None  # stdev / |mean| of EPS estimates
    target_price_consensus: float | None = None
    rating_distribution: Mapping[str, int] = field(default_factory=dict)
    forward_eps_growth: float | None = None  # FY2/FY1 - 1
    eps_revision_60d: float | None = None
    forward_eps_basis: str | None = None  # "NTM(FY1 40% + FY2 60%)" | "FY1"
    revision_status: Mapping[str, str] = field(default_factory=dict)  # {"7d": "READY", "90d": "ACCUMULATING 42/90일", ...}
    revision_basis: Mapping[str, str] = field(default_factory=dict)  # {"7d": "PROVIDER", "30d": "SELF", ...}
    estimate_range_pct: float | None = None  # (high − low) / |mean| of FY1 EPS estimates (not a standard deviation)
    cross_check: str | None = None  # CONSISTENT | DATA_CONFLICT | SEVERE_DATA_CONFLICT | SINGLE_SOURCE (+ detail)


@dataclass(frozen=True, slots=True)
class RevisionAssessment:
    eps_direction: int  # -1, 0, +1
    revenue_direction: int
    breadth_score: float | None  # 0..1 combining magnitude and consistency
    low_coverage: bool
    high_dispersion: bool


def assess_revisions(
    a: AnalystSnapshot | None,
    flat_band: float = 0.005,
    min_analysts: int = 5,
    high_dispersion: float = 0.25,
) -> RevisionAssessment | None:
    if a is None:
        return None

    def direction(*vals: float | None) -> int:
        vs = [v for v in vals if v is not None]
        if not vs:
            return 0
        avg = sum(vs) / len(vs)
        return 1 if avg > flat_band else -1 if avg < -flat_band else 0

    eps_vals = [v for v in (a.eps_revision_7d, a.eps_revision_30d, a.eps_revision_90d) if v is not None]
    breadth: float | None = None
    if eps_vals:
        # consistency across windows + magnitude of the 30d/90d revision
        agree = sum(1 for v in eps_vals if v > flat_band) - sum(1 for v in eps_vals if v < -flat_band)
        consistency = (agree / len(eps_vals) + 1) / 2
        mag = a.eps_revision_30d if a.eps_revision_30d is not None else eps_vals[-1]
        mag_score = max(0.0, min(1.0, 0.5 + mag * 10))  # +5% 30d revision → 1.0
        breadth = round(0.5 * consistency + 0.5 * mag_score, 4)
    return RevisionAssessment(
        eps_direction=direction(a.eps_revision_7d, a.eps_revision_30d, a.eps_revision_90d),
        revenue_direction=direction(a.revenue_revision_30d, a.revenue_revision_90d),
        breadth_score=breadth,
        low_coverage=(a.analyst_count or 0) < min_analysts,
        high_dispersion=(a.estimate_dispersion or 0) > high_dispersion,
    )


RELEASE_MAX_DAYS = 75  # 10-Q deadline is 40–45 days; an earnings release more than 75 days after the period is not its release


def pair_with_releases(rows: Sequence[Mapping[str, Any]], release_times: Sequence[datetime], source: str) -> list[EarningsReport]:
    """Fiscal-period EPS results (period end, actual, consensus) + the real announcement times (SEC 8-K Item
    2.02 acceptance times) → reports dated by their announcement. Each period takes the FIRST release after its
    end within ``RELEASE_MAX_DAYS``; one release belongs to one period. A period without a release is dropped —
    the period end is never used as a report date (it would make results visible weeks before they were public)."""
    times = sorted(release_times)
    used: set[datetime] = set()
    out: list[EarningsReport] = []
    for r in sorted(rows, key=lambda x: x["period"]):
        end: date = r["period"]
        t = next((x for x in times if x not in used and end < to_ny(x).date() <= end + timedelta(days=RELEASE_MAX_DAYS)), None)
        if t is None:
            continue
        used.add(t)
        q, y = r.get("quarter"), r.get("year")
        out.append(EarningsReport(report_date=to_ny(t).date(), fiscal_label=f"Q{q} {y}" if q and y else end.isoformat(), source=source,
                                  eps_actual=r.get("actual"), eps_consensus=r.get("estimate")))
    return out
