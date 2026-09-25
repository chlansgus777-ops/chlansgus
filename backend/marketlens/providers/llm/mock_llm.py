"""Deterministic MOCK LLM used in MOCK mode and tests. Output is derived only from the evidence pack and is
clearly labelled "(MOCK AI)". It never produces numbers."""

from __future__ import annotations

import json
from typing import Any

from marketlens.providers.llm.base import LLMResponse


def _section(user: str, header: str) -> Any:
    lines = user.split("\n")
    for i, ln in enumerate(lines):
        if ln.strip() == header and i + 1 < len(lines):
            try:
                return json.loads(lines[i + 1])
            except json.JSONDecodeError:
                return None
    return None


def _stance(sub: float | None) -> str:
    if sub is None:
        return "neutral"
    return "positive" if sub >= 0.6 else "negative" if sub <= 0.4 else "neutral"


class MockLLMProvider:
    name = "mock-llm"
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def complete_json(self, system: str, user: str, schema: dict[str, Any], tier: str, max_tokens: int = 4000) -> LLMResponse:
        self.calls += 1
        role = system.split("You are the ", 1)[1].split(" of the MarketLens", 1)[0].replace(" ", "_")
        pack = _section(user, "EVIDENCE_JSON:") or []
        ctx = _section(user, "COMMITTEE_CONTEXT_JSON:") or {}
        ids = [p["id"] for p in pack]
        out: dict[str, Any]
        if role in ("fundamental", "earnings", "valuation", "macro", "technical", "news", "risk_analyst"):
            sub = ctx.get("component_subscore")
            st = _stance(sub)
            conf = int(50 + abs((sub if sub is not None else 0.5) - 0.5) * 80)
            labels = [p["label"] for p in pack][:3]
            out = {
                "agent": role,
                "stance": st,
                "confidence": conf,
                "key_strengths": [f"{lb} reviewed" for lb in labels[:2]] if st != "negative" else [],
                "key_weaknesses": [f"{lb} is a concern" for lb in labels[:2]] if st == "negative" else [],
                "risks": ["evidence limited to the provided pack"],
                "missing_data": [] if pack else ["no evidence in this domain"],
                "evidence_ids": ids[:6],
                "summary": f"(MOCK AI) {role} view is {st}, derived from the deterministic evidence pack.",
            }
        elif role in ("bull", "bear"):
            rnd = int(ctx.get("round", 1))
            pts = [
                {
                    "claim": f"{'Upside' if role == 'bull' else 'Downside'} case rests on {p['label']}",
                    "evidence_ids": [p["id"]],
                    "interpretation": "supports the case" if role == "bull" else "argues for caution",
                    "rebuts": ("opposing interpretation of the same evidence" if rnd == 2 else None),
                }
                for p in pack[:3]
            ] or [{"claim": "no evidence available", "evidence_ids": ["NONE"], "interpretation": "", "rebuts": None}]
            out = {"side": role, "round": rnd, "thesis": f"(MOCK AI) {role} thesis round {rnd}", "points": pts}
        elif role == "synthesizer":
            cons = ctx.get("consensus_pct")
            stance = "positive" if (cons or 50) >= 60 else "negative" if (cons or 50) <= 40 else "neutral"
            out = {
                "committee_agreement": {"LOW": "HIGH", "MEDIUM": "MEDIUM", "HIGH": "LOW"}.get(ctx.get("divergence", "MEDIUM"), "MEDIUM"),
                "strongest_bull_argument": "(MOCK AI) strongest bull point is the best-scoring component",
                "strongest_bear_argument": "(MOCK AI) strongest bear point is the weakest component",
                "unresolved_uncertainty": ["mock committee cannot assess qualitative factors"],
                "advisory_stance": stance,
                "confidence_adjustment": 0,
                "evidence_ids": ids[:4],
            }
        elif role == "risk_manager":
            lvl = ctx.get("event_risk", "LOW")
            action = ctx.get("deterministic_action")
            rec = "BUY SMALL" if action == "BUY" and lvl in ("HIGH", "EXTREME") else None
            out = {"risk_level": lvl if lvl in ("LOW", "MEDIUM", "HIGH", "EXTREME") else "MEDIUM", "recommended_action": rec, "concerns": ["(MOCK AI) event and volatility risk reviewed"], "evidence_ids": ids[:4], "summary": "(MOCK AI) independent risk review"}
        else:  # portfolio_manager
            cap = ctx.get("size_cap", "HALF")
            out = {"portfolio_fit": ctx.get("fit", "NEUTRAL"), "suggested_size": cap, "concentration_warning": None, "overlap_risk": None, "evidence_ids": ids[:3], "summary": "(MOCK AI) sized within the deterministic concentration cap"}
        return LLMResponse(json.dumps(out), "mock-llm", len(user) // 4, 200, 1.0)
