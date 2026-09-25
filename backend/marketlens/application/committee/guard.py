"""Output guard: strict schema validation, evidence-ID validation and semantic numeric verification.

- Output must parse as the role's strict schema (extra fields are rejected).
- Evidence IDs that do not exist are removed and recorded. A debate point that cites ANY invalid ID is
  invalid as a whole (its grounding was fabricated). A report whose every citation is invalid is rejected;
  otherwise its confidence is scaled down by the share of invalid citations.
- Every number in free text must be supported semantically (entity, metric, unit, scale, sign, value) —
  see committee/claims.py. Debate points are checked only against the evidence they cite.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from marketlens.application.committee.claims import unverified
from marketlens.application.evidence import Evidence

M = TypeVar("M", bound=BaseModel)
REMOVED = "[삭제됨: 근거에 없는 수치가 포함된 문장]"


@dataclass
class GuardReport:
    rejected_claims: list[str] = field(default_factory=list)
    invalid_evidence_ids: list[str] = field(default_factory=list)
    schema_error: str | None = None
    reject_output: bool = False  # the whole output is ungrounded → treat as invalid
    confidence_scale: float = 1.0


def parse_strict(model: type[M], text: str) -> tuple[M | None, str | None]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"JSON 형식 오류: {e.msg}"
    if not isinstance(data, dict):
        return None, "최상위 JSON은 객체여야 함"
    try:
        return model.model_validate(data), None
    except ValidationError as e:
        return None, f"스키마 위반(schema violation): {e.errors()[0].get('msg', 'invalid')} at {e.errors()[0].get('loc')}"


def sanitize(obj: M, evidence: Sequence[Evidence], ticker: str, known_tickers: Iterable[str] = (), universe: Sequence[Evidence] | None = None) -> tuple[M, GuardReport]:
    """Return a cleaned copy of ``obj`` and a report of what was removed.

    ``evidence`` is what the role was shown (free-text numbers are verified against it); ``universe`` is
    every evidence item of the analysis (a debate point may cite any of them and is verified against the
    items it cites)."""
    rep = GuardReport()
    by_id = {e.evidence_id: e for e in (universe if universe is not None else evidence)}
    valid_ids = set(by_id)
    known = set(known_tickers) | {ticker}
    data = obj.model_dump()

    def bad_numbers(text: str, pool: Sequence[Evidence]) -> list[str]:
        return unverified(text, pool, ticker, known)

    def clean(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "evidence_ids" and isinstance(v, list):
                    good = [i for i in v if i in valid_ids]
                    bad = [i for i in v if i not in valid_ids]
                    rep.invalid_evidence_ids.extend(bad)
                    out[k] = good
                else:
                    out[k] = clean(v, f"{path}.{k}")
            return out
        if isinstance(node, list):
            kept = []
            for i, item in enumerate(node):
                if isinstance(item, str):
                    bad = bad_numbers(item, evidence)
                    if bad:
                        rep.rejected_claims.append(f"{path}[{i}]: {item[:160]} (검증 실패: {', '.join(bad)})")
                        continue
                    kept.append(item)
                else:
                    kept.append(clean(item, f"{path}[{i}]"))
            return kept
        if isinstance(node, str):
            bad = bad_numbers(node, evidence)
            if bad:
                rep.rejected_claims.append(f"{path}: {node[:160]} (검증 실패: {', '.join(bad)})")
                return REMOVED
            return node
        return node

    points = data.pop("points", None)
    cleaned = clean(data, type(obj).__name__)
    if points is not None:
        kept_points = []
        for i, p in enumerate(points):
            cited = list(p.get("evidence_ids") or [])
            invalid = [c for c in cited if c not in valid_ids]
            if invalid or not cited:
                rep.invalid_evidence_ids.extend(invalid)
                rep.rejected_claims.append(f"points[{i}]: {str(p.get('claim'))[:160]} (근거 ID 무효/없음 → 주장 무효: {', '.join(invalid) or '인용 없음'})")
                continue
            pool = [by_id[c] for c in cited if c in by_id] or list(evidence)
            texts = [p.get("claim") or "", p.get("interpretation") or "", p.get("rebuts") or ""]
            bad = [b for t in texts for b in bad_numbers(t, pool)]
            if bad:
                rep.rejected_claims.append(f"points[{i}]: {str(p.get('claim'))[:160]} (인용한 근거와 수치 불일치: {', '.join(bad)})")
                continue
            kept_points.append(p)
        if not kept_points:
            rep.rejected_claims.append("근거가 유효한 토론 논점이 하나도 없음")
            rep.reject_output = True
            kept_points = [{"claim": "[근거가 유효한 논점 없음]", "evidence_ids": sorted(valid_ids)[:1] or ["NONE"], "interpretation": "", "rebuts": None}]
        cleaned["points"] = kept_points
    cited_top = list(data.get("evidence_ids") or [])
    if cited_top:
        n_bad = sum(1 for c in cited_top if c not in valid_ids)
        if n_bad == len(cited_top):
            rep.reject_output = True  # every citation fabricated → ungrounded output
        elif n_bad:
            rep.confidence_scale = (len(cited_top) - n_bad) / len(cited_top)
    if "confidence" in cleaned and isinstance(cleaned["confidence"], int) and rep.confidence_scale < 1.0:
        cleaned["confidence"] = int(cleaned["confidence"] * rep.confidence_scale)
    return type(obj).model_validate(cleaned), rep
