"""Independent review of 059b465 (2026-10-06): the evaluator's own reproduction, run as delivered
(evaluations/independent_059b465/marketlens_review_repro.py — not edited). F01–F04 failed on 059b465; all six pass now."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_the_evaluators_reproduction_passes_unchanged():
    script = ROOT / "evaluations" / "independent_059b465" / "marketlens_review_repro.py"
    r = subprocess.run([sys.executable, str(script), "--source", str(ROOT)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-3000:]
    assert "Ran 6 tests" in r.stderr and "OK" in r.stderr
