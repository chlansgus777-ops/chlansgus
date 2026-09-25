"""Bull / Base / Bear scenarios. Probabilities stay N/A until calibrated."""

from __future__ import annotations

from dataclasses import dataclass

from marketlens.domain.entry import EntryPlan


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str  # Bull | Base | Bear
    trigger: str
    mechanism: str
    price_low: float
    price_high: float
    invalidation: str
    probability: float | None = None  # stays None until calibration supports it


def build_scenarios(plan: EntryPlan, atr: float, bull_trigger: str, bear_trigger: str, invalidation: str) -> list[Scenario]:
    p = plan.current_price
    return [
        Scenario("Bull", bull_trigger, "estimates move up and multiple holds", round(plan.target1, 2), round(plan.target2, 2), "bullish trigger fails to materialise"),
        Scenario("Base", "no major surprise", "price oscillates inside the recent range", round(p - atr, 2), round(p + atr, 2), "breakout above T1 or loss of the stop"),
        Scenario("Bear", bear_trigger, "estimate cuts or multiple compression", round(plan.stop - atr, 2), round(plan.stop, 2), invalidation),
    ]
