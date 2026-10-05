"""The screens' buy thresholds (frontend/src/thresholds.ts) are the model's own (config/scoring_model.toml [decision]):
the stock page said "매수 기준 80" after the model moved to 68 (owner 2026-10-05, a 66.7 BUY SMALL looked wrong)."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_screen_thresholds_are_the_models():
    dec = tomllib.loads((ROOT / "config" / "scoring_model.toml").read_text(encoding="utf-8"))["decision"]
    ts = (ROOT / "frontend" / "src" / "thresholds.ts").read_text(encoding="utf-8")
    got = {k: float(v) for k, v in re.findall(r"export const (BUY_SCORE|SMALL_SCORE) = (\d+(?:\.\d+)?);", ts)}
    assert got == {"BUY_SCORE": float(dec["buy_enter"]), "SMALL_SCORE": float(dec["buy_small_enter"])}


def test_no_screen_states_an_old_threshold_as_a_number():
    src = ROOT / "frontend" / "src"
    bad = [f"{p.relative_to(ROOT)}:{i}" for p in src.rglob("*.ts*") if ".test." not in p.name
           for i, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1)
           if re.search(r"매수 기준\s*\(?\d|\b(80|72)점? 이상", line)]
    assert bad == []
