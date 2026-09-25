"""Historical replay: recompute a past recommendation from its stored point-in-time snapshot only.

Current provider data is never fetched. The model configuration is rebuilt from the TOML texts stored
with the recommendation.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from marketlens.application.codec import decode
from marketlens.application.pipeline import AnalysisInputs, AnalysisResult, run_analysis
from marketlens.config import load_model_config


@dataclass(frozen=True)
class ReplayOutcome:
    matches: bool
    original_score: float
    replay_score: float
    original_action: str
    replay_action: str
    fingerprint_matches: bool
    result: AnalysisResult


AUDIT_ONLY_FILES = ("exposure_graph.toml", "theses.toml")  # their content is already inside AnalysisInputs


def config_snapshot(config_dir: Path, weights: dict[str, float], scoring_version: str) -> dict[str, Any]:
    snap: dict[str, Any] = {
        "scoring_model.toml": (config_dir / "scoring_model.toml").read_text(encoding="utf-8"),
        "sector_models.toml": (config_dir / "sector_models.toml").read_text(encoding="utf-8"),
        "weights": weights,
        "scoring_version": scoring_version,
    }
    for name in AUDIT_ONLY_FILES:
        p = config_dir / name
        if p.exists():
            snap[name] = p.read_text(encoding="utf-8")
    return snap


def replay(inputs_json: dict[str, Any], snapshot: dict[str, Any], original_score: float, original_action: str, original_fingerprint: str) -> ReplayOutcome:
    inp: AnalysisInputs = decode(AnalysisInputs, inputs_json)
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        (d / "scoring_model.toml").write_text(snapshot["scoring_model.toml"], encoding="utf-8")
        (d / "sector_models.toml").write_text(snapshot["sector_models.toml"], encoding="utf-8")
        cfg = load_model_config(d, weights_override=snapshot.get("weights"), scoring_version_override=snapshot.get("scoring_version"))
    res = run_analysis(inp, cfg)
    return ReplayOutcome(
        matches=abs(res.scorecard.total - original_score) < 1e-6 and res.decision.action.value == original_action,
        original_score=original_score,
        replay_score=res.scorecard.total,
        original_action=original_action,
        replay_action=res.decision.action.value,
        fingerprint_matches=inp.fingerprint() == original_fingerprint,
        result=res,
    )
