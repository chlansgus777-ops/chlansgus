"""Deterministic MOCK LLM used in MOCK mode and tests. Output is derived only from the evidence pack and is
clearly labelled "(MOCK AI)". It never produces numbers."""

from __future__ import annotations

import json
from typing import Any

from marketlens.providers.llm.base import LLMResponse

MOCK_LABEL = "(MOCK AI)"
MOCK_MODEL = "mock-llm"


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


_STANCE_KO = {"positive": "긍정적", "neutral": "중립", "negative": "부정적"}
_ROLE_KO = {"fundamental": "펀더멘털", "earnings": "실적", "valuation": "밸류에이션", "macro": "거시", "technical": "기술적", "news": "뉴스·이슈", "risk_analyst": "리스크"}


class MockLLMProvider:
    name = "mock-llm"
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def signature(self, tier: str) -> str:
        return f"mock-llm|{MOCK_MODEL}|{tier}"

    def complete_json(self, system: str, user: str, schema: dict[str, Any], tier: str, max_tokens: int = 4000) -> LLMResponse:
        self.calls += 1
        role = system.split("You are the ", 1)[1].split(" of the MarketLens", 1)[0].replace(" ", "_")
        pack = _section(user, "EVIDENCE_JSON:") or []
        ctx = _section(user, "COMMITTEE_CONTEXT_JSON:") or {}
        ids = [p["id"] for p in pack]
        out: dict[str, Any]
        if role in _ROLE_KO:
            sub = ctx.get("component_subscore")
            st = _stance(sub)
            conf = int(50 + abs((sub if sub is not None else 0.5) - 0.5) * 80)
            labels = [p["label"] for p in pack][:3]
            out = {
                "agent": role,
                "stance": st,
                "confidence": conf,
                "key_strengths": [f"{lb} 검토 결과 양호" for lb in labels[:2]] if st != "negative" else [],
                "key_weaknesses": [f"{lb} 우려" for lb in labels[:2]] if st == "negative" else [],
                "risks": ["제공된 근거 범위 안에서만 판단함"],
                "missing_data": [] if pack else ["이 영역의 근거 없음"],
                "evidence_ids": ids[:6],
                "summary": f"{MOCK_LABEL} {_ROLE_KO[role]} 관점: {_STANCE_KO[st]} — 결정론적 근거 묶음만으로 도출한 모의 의견",
            }
        elif role in ("bull", "bear"):
            rnd = int(ctx.get("round", 1))
            pts = [
                {
                    "claim": f"{'상승' if role == 'bull' else '하락'} 논거: {p['label']}",
                    "evidence_ids": [p["id"]],
                    "interpretation": "매수 논리를 지지" if role == "bull" else "신중론을 지지",
                    "rebuts": ("같은 근거에 대한 상대측 해석 반박" if rnd == 2 else None),
                }
                for p in pack[:3]
            ] or [{"claim": "근거 없음", "evidence_ids": ["NONE"], "interpretation": "", "rebuts": None}]
            out = {"side": role, "round": rnd, "thesis": f"{MOCK_LABEL} {'강세' if role == 'bull' else '약세'} 논리 {rnd}차", "points": pts}
        elif role == "synthesizer":
            cons = ctx.get("consensus_pct")
            stance = "positive" if (cons or 50) >= 60 else "negative" if (cons or 50) <= 40 else "neutral"
            out = {
                "committee_agreement": {"LOW": "HIGH", "MEDIUM": "MEDIUM", "HIGH": "LOW"}.get(ctx.get("divergence", "MEDIUM"), "MEDIUM"),
                "strongest_bull_argument": f"{MOCK_LABEL} 가장 강한 강세 논거는 점수가 가장 높은 구성요소",
                "strongest_bear_argument": f"{MOCK_LABEL} 가장 강한 약세 논거는 점수가 가장 낮은 구성요소",
                "unresolved_uncertainty": ["모의 위원회는 정성적 요인을 평가할 수 없음"],
                "advisory_stance": stance,
                "confidence_adjustment": 0,
                "evidence_ids": ids[:4],
            }
        elif role == "risk_manager":
            lvl = ctx.get("event_risk", "LOW")
            action = ctx.get("deterministic_action")
            rec = "BUY SMALL" if action == "BUY" and lvl in ("HIGH", "EXTREME") else None
            out = {"risk_level": lvl if lvl in ("LOW", "MEDIUM", "HIGH", "EXTREME") else "MEDIUM", "recommended_action": rec,
                   "concerns": [f"{MOCK_LABEL} 이벤트·변동성 위험 검토"], "evidence_ids": ids[:4], "summary": f"{MOCK_LABEL} 독립 리스크 검토"}
        else:  # portfolio_manager
            cap = ctx.get("size_cap", "HALF")
            out = {"portfolio_fit": ctx.get("fit", "NEUTRAL"), "suggested_size": cap, "concentration_warning": None, "overlap_risk": None,
                   "evidence_ids": ids[:3], "summary": f"{MOCK_LABEL} 결정론적 집중도 한도 안에서 비중 제안"}
        return LLMResponse(json.dumps(out, ensure_ascii=False), MOCK_MODEL, len(user) // 4, 200, 1.0)
