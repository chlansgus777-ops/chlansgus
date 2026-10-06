"""Independent review of 0444a21 (2026-10-06): the evaluator's own reproduction, run as delivered
(evaluations/independent_0444a21/marketlens_0444a21_repro.py — not edited). All nine failed on 0444a21; all pass now."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_the_evaluators_reproduction_passes_unchanged():
    script = ROOT / "evaluations" / "independent_0444a21" / "marketlens_0444a21_repro.py"
    r = subprocess.run([sys.executable, str(script), "--source", str(ROOT)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "Ran 9 tests" in r.stderr and "OK" in r.stderr
