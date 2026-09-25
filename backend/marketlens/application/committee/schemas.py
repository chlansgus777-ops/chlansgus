"""Strict structured-output schemas for every committee role (extra fields are forbidden)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

StanceT = Literal["positive", "neutral", "negative"]
AgentName = Literal["fundamental", "earnings", "valuation", "macro", "technical", "news", "risk_analyst"]
ANALYSTS: tuple[str, ...] = ("fundamental", "earnings", "valuation", "macro", "technical", "news", "risk_analyst")


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AgentReport(Strict):
    agent: AgentName
    stance: StanceT
    confidence: int = Field(ge=0, le=100)
    key_strengths: list[str] = Field(default_factory=list, max_length=6)
    key_weaknesses: list[str] = Field(default_factory=list, max_length=6)
    risks: list[str] = Field(default_factory=list, max_length=6)
    missing_data: list[str] = Field(default_factory=list, max_length=10)
    evidence_ids: list[str] = Field(default_factory=list, max_length=30)
    summary: str = Field(max_length=800)


class DebatePoint(Strict):
    claim: str = Field(max_length=400)
    evidence_ids: list[str] = Field(min_length=1, max_length=8)
    interpretation: str = Field(max_length=400)
    rebuts: str | None = Field(default=None, max_length=300)


class DebateArgument(Strict):
    side: Literal["bull", "bear"]
    round: int = Field(ge=1, le=2)
    thesis: str = Field(max_length=500)
    points: list[DebatePoint] = Field(min_length=1, max_length=6)


class Synthesis(Strict):
    committee_agreement: Literal["HIGH", "MEDIUM", "LOW"]
    strongest_bull_argument: str = Field(max_length=500)
    strongest_bear_argument: str = Field(max_length=500)
    unresolved_uncertainty: list[str] = Field(default_factory=list, max_length=6)
    advisory_stance: StanceT
    confidence_adjustment: float = Field(ge=-10, le=10)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)


ActionT = Literal["BUY", "BUY SMALL", "ADD", "HOLD", "WATCH", "WAIT", "REDUCE", "SELL", "DATA INSUFFICIENT"]


class RiskReview(Strict):
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "EXTREME"]
    recommended_action: ActionT | None = None
    concerns: list[str] = Field(default_factory=list, max_length=8)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)
    summary: str = Field(max_length=600)


class PortfolioAdvice(Strict):
    portfolio_fit: Literal["GOOD", "NEUTRAL", "POOR"]
    suggested_size: Literal["FULL", "HALF", "SMALL", "WATCH"]
    concentration_warning: str | None = Field(default=None, max_length=300)
    overlap_risk: str | None = Field(default=None, max_length=300)
    evidence_ids: list[str] = Field(default_factory=list, max_length=12)
    summary: str = Field(max_length=500)
