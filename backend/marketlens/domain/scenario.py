"""Bull / Base / Bear scenarios. Probabilities stay N/A until calibrated."""

from __future__ import annotations

from dataclasses import dataclass

from marketlens.domain.entry import EntryPlan


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str  # Bull | Base | Bear (UI label: 강세 / 기본 / 약세)
    trigger: str
    mechanism: str
    price_low: float
    price_high: float
    invalidation: str
    probability: float | None = None  # stays None until calibration supports it


def build_scenarios(plan: EntryPlan, atr: float, bull_trigger: str, bear_trigger: str, invalidation: str) -> list[Scenario]:
    p = plan.current_price
    return [
        Scenario("Bull", bull_trigger, "이익 추정치 상향 + 밸류에이션 배수 유지", round(plan.target1, 2), round(plan.target2, 2), "강세 촉매가 현실화되지 않음"),
        Scenario("Base", "큰 이변 없음", "최근 가격 범위 안에서 등락", round(p - atr, 2), round(p + atr, 2), "1차 목표가 돌파 또는 손절가 이탈"),
        Scenario("Bear", bear_trigger, "이익 추정치 하향 또는 밸류에이션 배수 축소", round(plan.stop - atr, 2), round(plan.stop, 2), invalidation),
    ]
