"""Build the analysis-time ``AnalystSnapshot`` from stored consensus snapshots (free providers).

Sources (all stored append-only in ``estimate_snapshots``):
- Alpha Vantage (final candidates only): FY1/FY2 and quarter consensus, analyst count, high/low, and the
  consensus 7/30/60/90 days ago as reported by the provider → PROVIDER revisions.
- Finnhub earnings calendar (whole market, daily): consensus for the next report → SELF revisions from
  MarketLens's own history ("ACCUMULATING n/90일" until enough days are stored).

Nothing is synthesised: no LLM guesses, no growth-extrapolated consensus, no price-implied revisions.
A value that is not known is None, and the window says why (UNKNOWN / ACCUMULATING).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Sequence

from marketlens.domain.earnings import AnalystSnapshot, EarningsReport
from marketlens.domain.estimates import WINDOWS, CrossCheck, EstimateObservation, RevisionValue, cross_check, ntm_eps, provider_revision, same_quarter, self_revision

AV = "alphavantage"
FINNHUB = "finnhub"
MAX_SNAPSHOT_AGE_DAYS = 7  # an estimate snapshot older than this is not "current consensus"


@dataclass(frozen=True, slots=True)
class EstimateReport:
    snapshot: AnalystSnapshot | None
    revisions: dict[str, RevisionValue]
    cross: CrossCheck
    sources: tuple[str, ...]
    notes: tuple[str, ...]


def _latest_day(hist: Sequence[EstimateObservation], provider: str, as_of: date) -> date | None:
    days = [h.observed_on for h in hist if h.provider == provider and h.observed_on <= as_of]
    return max(days) if days else None


def build(ticker: str, history: Sequence[EstimateObservation], as_of: date) -> EstimateReport:
    hist = [h for h in history if h.observed_on <= as_of]  # point in time: never a later snapshot
    notes: list[str] = []
    av_day = _latest_day(hist, AV, as_of)
    fh_day = _latest_day(hist, FINNHUB, as_of)
    av_now = [h for h in hist if h.provider == AV and h.observed_on == av_day] if av_day and (as_of - av_day).days <= MAX_SNAPSHOT_AGE_DAYS else []
    fh_now = [h for h in hist if h.provider == FINNHUB and h.observed_on == fh_day] if fh_day and (as_of - fh_day).days <= MAX_SNAPSHOT_AGE_DAYS else []
    if av_day and not av_now:
        notes.append(f"Alpha Vantage 스냅샷이 {(as_of - av_day).days}일 전 것이라 현재 컨센서스로 쓰지 않음")

    annual = sorted((h for h in av_now if h.period_type == "annual" and h.period_end and h.period_end >= as_of), key=lambda h: h.period_end or date.max)
    fy1 = annual[0] if annual else None
    fy2 = annual[1] if len(annual) > 1 else None
    fwd_eps, basis = ntm_eps(fy1, fy2, as_of)
    fwd_rev = None
    if fy1 is not None and fy1.revenue is not None:
        if fy2 is not None and fy2.revenue is not None and fy1.period_end is not None:
            left = max(0.0, min(1.0, (fy1.period_end - as_of).days / 365.0))
            fwd_rev = left * fy1.revenue + (1 - left) * fy2.revenue
        else:
            fwd_rev = fy1.revenue
    growth = (fy2.eps / fy1.eps - 1) if fy1 and fy2 and fy1.eps and fy2.eps and fy1.eps > 0 and fy2.eps > 0 else None

    # the next report's consensus from the calendar, and the matching provider quarter for a cross-check
    nxt = min((h for h in fh_now if h.report_date and h.report_date >= as_of), key=lambda h: h.report_date or date.max, default=None)
    av_q = next((q for q in av_now if q.period_type == "quarter" and nxt is not None and same_quarter(q, nxt)), None)
    cc = cross_check(av_q, nxt)

    revs: dict[str, RevisionValue] = {}
    for w in WINDOWS:
        rv: RevisionValue | None = None
        if fy1 is not None:
            rv = provider_revision(fy1, w)
            if rv.status != "READY":  # the provider did not report that window → our own FY1 history
                own = self_revision([h for h in hist if h.provider == AV and h.period == fy1.period], "eps", as_of, w)
                rv = own if own.status == "READY" or rv.status == "UNKNOWN" else rv
        if (rv is None or rv.status != "READY") and nxt is not None:
            own = self_revision([h for h in hist if h.provider == FINNHUB and h.period == nxt.period], "eps", as_of, w)
            if rv is None or own.status == "READY" or (own.status == "ACCUMULATING" and rv.status == "UNKNOWN"):
                rv = own
        revs[f"{w}d"] = rv or RevisionValue(w, None, "UNKNOWN", "NONE", "추정치 공급원 없음")
    rev_rev = {}
    for w in (30, 90):
        src = [h for h in hist if fy1 is not None and h.provider == AV and h.period == fy1.period] or [h for h in hist if nxt is not None and h.provider == FINNHUB and h.period == nxt.period]
        rev_rev[w] = self_revision(src, "revenue", as_of, w)

    if not av_now and not fh_now:
        return EstimateReport(None, revs, cc, (), tuple(notes + ["무료 추정치 스냅샷 없음(MISSING)"]))
    sources = tuple(p for p, ok in ((AV, bool(av_now)), (FINNHUB, bool(fh_now))) if ok)
    rng = (fy1.eps_high - fy1.eps_low) / abs(fy1.eps) if fy1 and fy1.eps and fy1.eps_high is not None and fy1.eps_low is not None else None
    snap = AnalystSnapshot(
        as_of=max(d for d in (av_day if av_now else None, fh_day if fh_now else None) if d is not None),
        source="+".join(sources),
        forward_eps=round(fwd_eps, 6) if fwd_eps is not None else None,
        forward_revenue=round(fwd_rev, 2) if fwd_rev is not None else None,
        eps_revision_7d=revs["7d"].value, eps_revision_30d=revs["30d"].value, eps_revision_60d=revs["60d"].value, eps_revision_90d=revs["90d"].value,
        revenue_revision_30d=rev_rev[30].value, revenue_revision_90d=rev_rev[90].value,
        analyst_count=fy1.analyst_count if fy1 else None,
        estimate_dispersion=None,  # a standard deviation is not published by the free sources
        forward_eps_growth=round(growth, 6) if growth is not None else None,
        forward_eps_basis=basis if fwd_eps is not None else None,
        revision_status={k: v.status if v.status != "ACCUMULATING" else f"ACCUMULATING {v.detail.split()[-1]}" for k, v in revs.items()},
        revision_basis={k: v.basis for k, v in revs.items()},
        estimate_range_pct=round(rng, 6) if rng is not None else None,
        cross_check=f"{cc.status}: {cc.detail}",
    )
    return EstimateReport(snap, revs, cc, sources, tuple(notes))


# ------------------------------------------------------------------ guidance vs the consensus of the day


def _mid(r: object) -> float | None:
    lo, hi = getattr(r, "low", None), getattr(r, "high", None)
    return (lo + hi) / 2 if lo is not None and hi is not None else None


def attach_guidance(reports: Sequence[EarningsReport], rows: Sequence[object], history: Sequence[EstimateObservation], as_of: date) -> list[EarningsReport]:
    """Put the latest SEC-extracted guidance (filed on/before ``as_of``) on the earnings report released
    with it, next to the consensus that was known *before* the release (a snapshot observed earlier).
    Without a pre-release snapshot the consensus stays None — the comparison is then not made."""
    from dataclasses import replace

    from marketlens.domain.earnings import Guidance

    ok = [r for r in rows if getattr(r, "status", "") == "EXTRACTED" and getattr(r, "filed_at").date() <= as_of]
    if not ok:
        return list(reports)
    last_acc = max(ok, key=lambda r: getattr(r, "filed_at")).accession  # type: ignore[attr-defined]
    items = [r for r in ok if r.accession == last_acc]  # type: ignore[attr-defined]
    filed = items[0].filed_at.date()  # type: ignore[attr-defined]
    target = next((i for i, rep in enumerate(reports) if abs((rep.report_date - filed).days) <= 3), None)
    if target is None:
        return list(reports)

    def pick(metric: str, quarter: bool) -> object | None:
        for r in items:
            lab = (r.period_label or "").lower()  # type: ignore[attr-defined]
            is_q = "quarter" in lab or lab.startswith("q")
            if r.metric == metric and is_q == quarter and r.period_label:  # type: ignore[attr-defined]
                return r
        return None

    q_rev, q_eps, fy_rev, fy_eps, gm = pick("revenue", True), pick("eps", True), pick("revenue", False), pick("eps", False), pick("gross_margin", True) or pick("gross_margin", False)
    before = [h for h in history if h.observed_on < filed]
    nxt = min((h for h in before if h.provider == FINNHUB and h.report_date and h.report_date > filed + timedelta(days=20)),
              key=lambda h: (h.report_date or date.max, -h.observed_on.toordinal()), default=None)
    if nxt is not None:  # the latest snapshot of that period taken before the release
        nxt = max((h for h in before if h.provider == FINNHUB and h.period == nxt.period), key=lambda h: h.observed_on)
    used = [r for r in (q_rev, q_eps, fy_rev, fy_eps, gm) if r is not None]
    g = Guidance(
        next_q_revenue_low=getattr(q_rev, "low", None), next_q_revenue_high=getattr(q_rev, "high", None),
        next_q_revenue_consensus=nxt.revenue if nxt is not None and q_rev is not None else None,
        next_q_eps_low=getattr(q_eps, "low", None), next_q_eps_high=getattr(q_eps, "high", None),
        next_q_eps_consensus=nxt.eps if nxt is not None and q_eps is not None else None,
        fy_revenue_mid=_mid(fy_rev) if fy_rev else None, fy_eps_mid=_mid(fy_eps) if fy_eps else None,
        gross_margin_guide=_mid(gm) if gm else None,
        source=items[0].source_url, evidence=" | ".join(r.sentence for r in used)[:900] or None,  # type: ignore[attr-defined]
        confidence=min((r.confidence for r in used), default=None, key=lambda c: 0 if c == "LOW" else 1),  # type: ignore[attr-defined]
    )
    out = list(reports)
    out[target] = replace(out[target], guidance=g)
    return out
