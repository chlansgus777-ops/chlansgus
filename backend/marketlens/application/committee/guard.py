"""Output guard: schema validation, evidence-ID validation and numeric-claim verification.

An agent may not introduce numbers that are not in its evidence pack. Any text item with an unverified
number is removed and recorded; invalid evidence IDs are dropped and recorded.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

M = TypeVar("M", bound=BaseModel)

NUM_RE = re.compile(r"(?<![A-Za-z0-9_.])[-+]?\$?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?(?![A-Za-z0-9])")
# small integers and common look-back windows (e.g. "52-week", "200-day") are not data claims
SAFE_SMALL_INTS = set(range(0, 11)) | {14, 20, 30, 50, 52, 60, 90, 100, 200}


@dataclass
class GuardReport:
    rejected_claims: list[str] = field(default_factory=list)
    invalid_evidence_ids: list[str] = field(default_factory=list)
    schema_error: str | None = None


def _numbers(text: str) -> list[tuple[str, float, bool]]:
    out = []
    for m in NUM_RE.finditer(text):
        raw = m.group(0)
        pct = raw.endswith("%")
        v = raw.replace("$", "").replace(",", "").rstrip("%").lstrip("+")
        try:
            out.append((raw, float(v), pct))
        except ValueError:
            continue
    return out


def allowed_numbers(values: list[Any]) -> list[float]:
    nums: list[float] = []
    for v in values:
        if isinstance(v, bool) or v is None:
            continue
        if isinstance(v, (int, float)):
            f = float(v)
            nums.extend([f, f * 100, abs(f), abs(f) * 100])
        elif isinstance(v, str):
            nums.extend(n for _, n, _ in _numbers(v))
    return nums


def number_is_supported(n: float, allowed: list[float], rel_tol: float = 0.02) -> bool:
    if n.is_integer() and int(abs(n)) in SAFE_SMALL_INTS:
        return True
    if 1990 <= n <= 2100 and n.is_integer():  # years / fiscal labels
        return True
    for a in allowed:
        if a == 0:
            if abs(n) < 1e-9:
                return True
            continue
        if abs(n - a) / abs(a) <= rel_tol or abs(n - a) <= 0.051:
            return True
    return False


def unverified_numbers(text: str, allowed: list[float]) -> list[str]:
    return [raw for raw, n, _ in _numbers(text) if not number_is_supported(abs(n), [abs(a) for a in allowed])]


def parse_strict(model: type[M], text: str) -> tuple[M | None, str | None]:
    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        return None, f"invalid JSON: {e.msg}"
    if not isinstance(data, dict):
        return None, "top-level JSON must be an object"
    try:
        return model.model_validate(data), None
    except ValidationError as e:
        return None, f"schema violation: {e.errors()[0].get('msg', 'invalid')} at {e.errors()[0].get('loc')}"


def sanitize(obj: M, valid_ids: set[str], allowed: list[float]) -> tuple[M, GuardReport]:
    """Return a cleaned copy of ``obj`` and a report of what was removed."""
    rep = GuardReport()
    data = obj.model_dump()

    def clean(node: Any, path: str) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k == "evidence_ids" and isinstance(v, list):
                    good = [i for i in v if i in valid_ids]
                    rep.invalid_evidence_ids.extend(i for i in v if i not in valid_ids)
                    out[k] = good
                else:
                    out[k] = clean(v, f"{path}.{k}")
            return out
        if isinstance(node, list):
            kept = []
            for i, item in enumerate(node):
                if isinstance(item, str):
                    bad = unverified_numbers(item, allowed)
                    if bad:
                        rep.rejected_claims.append(f"{path}[{i}]: {item[:160]} (unverified: {', '.join(bad)})")
                        continue
                    kept.append(item)
                else:
                    kept.append(clean(item, f"{path}[{i}]"))
            return kept
        if isinstance(node, str):
            bad = unverified_numbers(node, allowed)
            if bad:
                rep.rejected_claims.append(f"{path}: {node[:160]} (unverified: {', '.join(bad)})")
                return "[removed: contained numbers not present in the evidence]"
            return node
        return node

    cleaned = clean(data, type(obj).__name__)
    # debate points must still cite at least one valid evidence id
    if "points" in cleaned:
        cleaned["points"] = [p for p in cleaned["points"] if p.get("evidence_ids")]
        if not cleaned["points"]:
            rep.rejected_claims.append("all debate points lacked valid evidence")
            cleaned["points"] = [{"claim": "[no evidence-backed argument]", "evidence_ids": sorted(valid_ids)[:1] or ["NONE"], "interpretation": "", "rebuts": None}]
    return type(obj).model_validate(cleaned), rep
