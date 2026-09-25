"""Deterministic committee consensus and divergence (no averaging of raw stances)."""

from __future__ import annotations

from marketlens.application.committee.schemas import AgentReport

BASE_RELEVANCE = {"fundamental": 1.0, "earnings": 1.0, "valuation": 0.9, "macro": 0.7, "technical": 0.5, "news": 0.7, "risk_analyst": 0.8}
MACRO_HEAVY_MODELS = {"financial", "reit", "utilities", "energy"}
STANCE_VALUE = {"positive": 1.0, "neutral": 0.0, "negative": -1.0}


def relevance(agent: str, sector_model: str) -> float:
    r = BASE_RELEVANCE.get(agent, 0.5)
    if agent == "macro" and sector_model in MACRO_HEAVY_MODELS:
        r = 1.0
    return r


def consensus(reports: dict[str, AgentReport], sector_model: str) -> tuple[float | None, str | None]:
    """Return (bullish consensus %, divergence level)."""
    items = []
    for name, rep in reports.items():
        completeness = 1.0 - min(0.5, 0.1 * len(rep.missing_data))
        w = relevance(name, sector_model) * (rep.confidence / 100) * completeness
        items.append((STANCE_VALUE[rep.stance], w))
    tw = sum(w for _, w in items)
    if not items or tw <= 0:
        return None, None
    mean = sum(s * w for s, w in items) / tw
    var = sum(w * (s - mean) ** 2 for s, w in items) / tw
    std = var**0.5
    divergence = "HIGH" if std > 0.7 else "MEDIUM" if std > 0.4 else "LOW"
    return round((mean + 1) / 2 * 100, 1), divergence


DIVERGENCE_CONFIDENCE_PENALTY = {"HIGH": -8.0, "MEDIUM": -3.0, "LOW": 0.0}
