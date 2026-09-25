"""Enumerations shared across the domain."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - trivial
        return self.value


class DataMode(StrEnum):
    MOCK = "MOCK"
    LIVE = "LIVE"


class DataQuality(StrEnum):
    FRESH = "FRESH"
    DELAYED = "DELAYED"
    STALE = "STALE"
    CONFLICTING = "CONFLICTING"
    MISSING = "MISSING"


class TradingSession(StrEnum):
    PREMARKET = "PREMARKET"
    REGULAR = "REGULAR"
    AFTER_HOURS = "AFTER_HOURS"
    OVERNIGHT = "OVERNIGHT"
    CLOSED = "CLOSED"


class Exchange(StrEnum):
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"
    NYSE_AMERICAN = "NYSE American"


class Action(StrEnum):
    BUY = "BUY"
    BUY_SMALL = "BUY SMALL"
    ADD = "ADD"
    HOLD = "HOLD"
    WATCH = "WATCH"
    WAIT = "WAIT"
    REDUCE = "REDUCE"
    SELL = "SELL"
    DATA_INSUFFICIENT = "DATA INSUFFICIENT"


BULLISH_ACTIONS = frozenset({Action.BUY, Action.BUY_SMALL, Action.ADD})

ACTION_KO = {
    Action.BUY: "매수",
    Action.BUY_SMALL: "소량 매수",
    Action.ADD: "추가 매수",
    Action.HOLD: "보유",
    Action.WATCH: "관찰",
    Action.WAIT: "대기",
    Action.REDUCE: "비중 축소",
    Action.SELL: "매도",
    Action.DATA_INSUFFICIENT: "데이터 부족",
}


class HardVeto(StrEnum):
    STALE_PRICE = "STALE_PRICE"
    STALE_CORE_DATA = "STALE_CORE_DATA"
    MISSING_CORE_DATA = "MISSING_CORE_DATA"
    SEVERE_DATA_CONFLICT = "SEVERE_DATA_CONFLICT"
    THESIS_INVALIDATED = "THESIS_INVALIDATED"
    UNACCEPTABLE_LIQUIDITY = "UNACCEPTABLE_LIQUIDITY"
    EXTREME_EVENT_RISK = "EXTREME_EVENT_RISK"


class Stance(StrEnum):
    POSITIVE = "positive"
    NEUTRAL = "neutral"
    NEGATIVE = "negative"


class SizeClass(StrEnum):
    FULL = "FULL"
    HALF = "HALF"
    SMALL = "SMALL"
    WATCH = "WATCH"


class Horizon(StrEnum):
    IMMEDIATE = "IMMEDIATE"  # 0-1 trading day
    SHORT = "SHORT"  # 1-5 trading days
    SWING = "SWING"  # 2-6 weeks
    FUNDAMENTAL = "FUNDAMENTAL"  # 1-4 quarters


class IssueCategory(StrEnum):
    EARNINGS = "Earnings"
    GUIDANCE = "Guidance"
    AI = "AI"
    REGULATION = "Regulation"
    EXPORT_CONTROL = "Export Control"
    TARIFF = "Tariff"
    GEOPOLITICS = "Geopolitics"
    MA = "M&A"
    PRODUCT = "Product"
    COMPETITION = "Competition"
    SUPPLY_CHAIN = "Supply Chain"
    RATES = "Rates"
    INFLATION = "Inflation"
    OIL = "Oil"
    LEGAL = "Legal"
    ANTITRUST = "Antitrust"
    FINANCING = "Financing"
    DILUTION = "Dilution"
    BUYBACK = "Buyback"
    MANAGEMENT = "Management"
    CYBERSECURITY = "Cybersecurity"


class ConfirmedStatus(StrEnum):
    CONFIRMED = "CONFIRMED"
    REPORTED = "REPORTED"
    RUMOR = "RUMOR"


class NodeType(StrEnum):
    COMPANY = "Company"
    SECTOR = "Sector"
    INDUSTRY = "Industry"
    COUNTRY = "Country"
    COMMODITY = "Commodity"
    MACRO_FACTOR = "MacroFactor"
    THEME = "TechnologyTheme"


class EdgeType(StrEnum):
    SUPPLIER_OF = "SUPPLIER_OF"
    CUSTOMER_OF = "CUSTOMER_OF"
    COMPETITOR_OF = "COMPETITOR_OF"
    PARTNER_OF = "PARTNER_OF"
    DEPENDS_ON = "DEPENDS_ON"
    PROVIDES_TO = "PROVIDES_TO"
    EXPOSED_TO_REGION = "EXPOSED_TO_REGION"
    EXPOSED_TO_RATE = "EXPOSED_TO_RATE"
    EXPOSED_TO_COMMODITY = "EXPOSED_TO_COMMODITY"
    EXPOSED_TO_AI = "EXPOSED_TO_AI"
    EXPOSED_TO_CLOUD = "EXPOSED_TO_CLOUD"


class ProviderStatus(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    RATE_LIMITED = "RATE_LIMITED"
    STALE = "STALE"
    DOWN = "DOWN"


class BreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class ExitReason(StrEnum):
    STOP = "STOP"
    TARGET_1 = "TARGET_1"
    TARGET_2 = "TARGET_2"
    TIME_EXIT = "TIME_EXIT"
    THESIS_INVALIDATION = "THESIS_INVALIDATION"
    RECOMMENDATION_DOWNGRADE = "RECOMMENDATION_DOWNGRADE"
