"""Regression guard: scoring / decision / entry / what-changed / issue impact on frozen fixtures must not
drift unintentionally. Intentional model changes: bump the version in config and regenerate with
UPDATE_BASELINE=1 python -m pytest tests/regression."""

import json
import os
from pathlib import Path

from marketlens.application.codec import decode
from marketlens.application.pipeline import AnalysisInputs, run_analysis
from marketlens.domain.enums import Horizon

FIX = Path(__file__).parents[1] / "golden" / "fixtures"
BASELINE = Path(__file__).parent / "baseline.json"


def snapshot(cfg) -> dict:
    out = {}
    for f in sorted(FIX.glob("*.json")):
        r = run_analysis(decode(AnalysisInputs, json.loads(f.read_text())), cfg)
        out[f.stem] = {
            "scoring_model_version": cfg.scoring_model.version,
            "total": r.scorecard.total,
            "components": {c.name: c.subscore for c in r.scorecard.components},
            "action": r.decision.action.value,
            "raw_action": r.decision.raw_action.value,
            "vetoes": [v.value for v in r.decision.vetoes],
            "entry": None if r.entry is None else [r.entry.stop, r.entry.max_buy, r.entry.target1, r.entry.rr_at_current],
            "issues": {i.issue_id: i.at(Horizon.SWING).impact_score for i in r.issue_impacts},
            "changes": [c.text for c in r.changes if c.material],
        }
    return out


def test_regression_baseline(cfg):
    snap = snapshot(cfg)
    if os.environ.get("UPDATE_BASELINE") == "1" or not BASELINE.exists():
        BASELINE.write_text(json.dumps(snap, indent=1, sort_keys=True))
    base = json.loads(BASELINE.read_text())
    assert snap == base, "model output drifted — if intentional, bump scoring_model_version and regenerate the baseline"
