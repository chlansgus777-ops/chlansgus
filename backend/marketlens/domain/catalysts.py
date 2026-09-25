"""Catalyst calendar and event-risk assessment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from marketlens.domain.enums import StrEnum
from marketlens.domain.market_calendar import trading_days_between


class CatalystType(StrEnum):
    EARNINGS = "Earnings"
    FED = "Fed"
    CPI = "CPI"
    PCE = "PCE"
    JOBS = "Jobs"
    GDP = "GDP"
    INVESTOR_DAY = "Investor Day"
    PRODUCT_LAUNCH = "Product Launch"
    CONFERENCE = "Conference"
    REGULATORY_DECISION = "Regulatory Decision"
    ANTITRUST_DECISION = "Antitrust Decision"
    GOVERNMENT_POLICY = "Government Policy"
    FDA_DECISION = "FDA Decision"


BINARY_EVENTS = frozenset({CatalystType.FDA_DECISION, CatalystType.ANTITRUST_DECISION, CatalystType.REGULATORY_DECISION})
MACRO_EVENTS = frozenset({CatalystType.FED, CatalystType.CPI, CatalystType.PCE, CatalystType.JOBS, CatalystType.GDP})


@dataclass(frozen=True, slots=True)
class CatalystEvent:
    event_id: str
    event_type: CatalystType
    event_date: date
    title: str
    affected: tuple[str, ...]  # tickers; empty for market-wide macro events
    importance: float  # 0..1
    expected_move: float | None = None  # fraction, from options when available
    source: str = ""

    def days_until(self, today: date) -> int:
        return trading_days_between(today, self.event_date) if self.event_date >= today else -1


@dataclass(frozen=True, slots=True)
class EventRisk:
    level: str  # LOW | MEDIUM | HIGH | EXTREME
    nearest: CatalystEvent | None
    days_until: int | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EventRiskConfig:
    earnings_window_days: int = 5
    binary_window_days: int = 3
    extreme_expected_move: float = 0.15
    high_expected_move: float = 0.08


def assess_event_risk(ticker: str, events: list[CatalystEvent], today: date, cfg: EventRiskConfig | None = None) -> EventRisk:
    cfg = cfg or EventRiskConfig()
    relevant = [e for e in events if (ticker in e.affected or (not e.affected and e.event_type in MACRO_EVENTS)) and e.event_date >= today]
    relevant.sort(key=lambda e: e.event_date)
    level = "LOW"
    reasons: list[str] = []
    rank = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "EXTREME": 3}

    def bump(new: str, why: str) -> None:
        nonlocal level
        if rank[new] > rank[level]:
            level = new
        reasons.append(why)

    for e in relevant:
        dte = e.days_until(today)
        if e.event_type in BINARY_EVENTS and ticker in e.affected and dte <= cfg.binary_window_days:
            bump("EXTREME", f"binary event '{e.title}' in {dte} trading day(s)")
        if e.event_type == CatalystType.EARNINGS and ticker in e.affected and dte <= cfg.earnings_window_days:
            if e.expected_move is not None and e.expected_move >= cfg.extreme_expected_move:
                bump("EXTREME", f"earnings in {dte}d with implied move ±{e.expected_move:.0%}")
            elif e.expected_move is not None and e.expected_move >= cfg.high_expected_move:
                bump("HIGH", f"earnings in {dte}d with implied move ±{e.expected_move:.0%}")
            else:
                bump("MEDIUM", f"earnings in {dte} trading day(s)")
        if e.event_type in MACRO_EVENTS and dte <= 1 and e.importance >= 0.8:
            bump("MEDIUM", f"high-importance macro release '{e.title}' within 1 day")
    nearest = relevant[0] if relevant else None
    return EventRisk(level, nearest, nearest.days_until(today) if nearest else None, tuple(reasons))
