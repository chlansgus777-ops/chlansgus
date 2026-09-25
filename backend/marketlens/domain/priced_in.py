"""Priced-in estimator.

Output is an *estimate* (0..100) with a confidence that reflects how many inputs were available.
It must never be presented as a fact.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PricedInInputs:
    event_direction: int  # +1 good news, -1 bad news
    pre_event_return: float | None = None  # stock return over the run-up window (e.g. 10d before)
    pre_event_benchmark_return: float | None = None
    daily_volatility: float | None = None  # stdev of daily returns
    run_up_days: int = 10
    abnormal_volume_ratio: float | None = None  # run-up volume / normal volume
    gap_reaction: float | None = None  # first-session gap after the event, signed
    implied_move: float | None = None  # options expected move (fraction)
    iv_rank: float | None = None  # 0..1
    revision_already_in_direction: float | None = None  # 30d EPS revision (signed fraction)
    news_repetition: int | None = None  # number of prior articles on the theme
    days_since_first_report: int | None = None


@dataclass(frozen=True, slots=True)
class PricedInEstimate:
    value: float | None  # 0..100
    confidence: float  # 0..1
    components: tuple[tuple[str, float], ...]
    label: str = "estimate"


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def estimate_priced_in(x: PricedInInputs) -> PricedInEstimate:
    comps: list[tuple[str, float]] = []
    d = 1 if x.event_direction >= 0 else -1
    if x.pre_event_return is not None and x.daily_volatility:
        excess = x.pre_event_return - (x.pre_event_benchmark_return or 0.0)
        z = excess / (x.daily_volatility * (x.run_up_days**0.5))
        comps.append(("abnormal_run_up", _clip01(0.5 + d * z / 4)))
    if x.abnormal_volume_ratio is not None:
        comps.append(("abnormal_volume", _clip01((x.abnormal_volume_ratio - 1.0) / 2.0)))
    if x.gap_reaction is not None and x.implied_move:
        # a reaction that already used up the implied move means the market has repriced
        comps.append(("gap_vs_expected_move", _clip01(d * x.gap_reaction / x.implied_move)))
    if x.iv_rank is not None:
        # elevated IV into the event → uncertainty acknowledged by the market
        comps.append(("iv_rank", _clip01(x.iv_rank)))
    if x.revision_already_in_direction is not None:
        comps.append(("revisions_moved", _clip01(0.5 + d * x.revision_already_in_direction * 10)))
    if x.news_repetition is not None:
        comps.append(("news_repetition", _clip01(x.news_repetition / 20)))
    if x.days_since_first_report is not None:
        comps.append(("time_since_first_report", _clip01(x.days_since_first_report / 10)))
    total_inputs = 7
    if not comps:
        return PricedInEstimate(None, 0.0, ())
    value = sum(v for _, v in comps) / len(comps) * 100
    confidence = len(comps) / total_inputs
    return PricedInEstimate(round(value, 1), round(confidence, 3), tuple((k, round(v, 3)) for k, v in comps))
