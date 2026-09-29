"""The 성과 screen's 과거 검증 section (PREREGISTRATION §12: shown whether or not anything is adopted): a compact,
committed copy of one measured run — never written by hand, never a sample.

    python -m marketlens.backtest.summary --results bt-out --run-id 123 --out config/backtest_results.json

It keeps what the screen shows (per-element verdicts at h = 60, the score quintiles at 1× cost / 30 % delisting, the
app-rule strategy, the §12/§13 decisions) and where it came from (the results.json SHA-256, the data SHA-256, the
commit, the workflow run), so the numbers can be traced back to the run that produced them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

MAIN = "cost1x_delist30"
DISCLAIMER = "과거 시뮬레이션이며 미래 수익을 보장하지 않습니다."
ELEMENT_KEYS = ("verdict", "mean_ic", "holm_p", "weeks", "coverage_mean", "criteria")


def _pick(d: dict[str, Any] | None, keys: tuple[str, ...]) -> dict[str, Any]:
    return {k: d.get(k) for k in keys} if d else {}


def summarize(results: dict[str, Any], manifest: dict[str, Any], results_sha256: str, leak: dict[str, Any] | None = None,
              run_id: str | None = None) -> dict[str, Any]:
    h60 = results["horizons"]["60"]
    h20 = results["horizons"].get("20", {})
    elements = {}
    for el, e in sorted(h60["elements"].items()):
        elements[el] = _pick(e, ELEMENT_KEYS) | {"mean_ic_h20": (h20.get("elements", {}).get(el) or {}).get("mean_ic")}
    q = h60["quintiles"].get(MAIN, {})
    strat = results.get("strategy", {}).get(MAIN, {})
    app = results.get("application") or {}
    run = results.get("run") or {}
    return {
        "available": True,
        "source": {"results_sha256": results_sha256, "data_sha256": manifest.get("data_sha256"), "commit": manifest.get("commit"),
                   "scoring_version": manifest.get("scoring_version"), "workflow_run": run_id, "finished_at": manifest.get("finished_at")},
        "window": run.get("window"), "weeks": len(run.get("weeks") or []), "weeks_used_h60": h60.get("weeks_used"),
        "data": app.get("data"), "periods": app.get("periods"),
        "elements": elements,
        "quintiles": {"assumption": "비용 1× · 상장폐지 손실 30% · 60거래일",
                      "rows": {k: _pick(v, ("mean_ret", "excess_spy", "hit_rate_vs_spy")) for k, v in q.items() if k != "top_minus_bottom"},
                      "top_minus_bottom": q.get("top_minus_bottom")},
        "strategy": {"assumption": "비용 1× · 상장폐지 손실 30%", **{k: strat.get(k) for k in (
            "start", "end", "cagr", "spy_cagr", "excess_cagr", "sharpe", "volatility", "max_drawdown", "trades", "avg_holding_sessions",
            "turnover_per_year", "weekly_excess_ci90", "equity_weekly")}},
        "application": {k: app.get(k) for k in ("s12", "s13", "final_weights", "operating_weights", "changed")},
        "leak_checks": {"truncated_all_equal": (leak or {}).get("truncated_all_equal"), "canary": (leak or {}).get("canary"),
                        "shuffle_mean_ic": (results.get("shuffle_check") or {}).get("mean_ic")},
        "disclaimer": DISCLAIMER,
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True, help="the run's output directory (results.json, manifest.json, leak_checks.json)")
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    d = Path(a.results)
    raw = (d / "results.json").read_bytes()
    manifest = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
    sha = hashlib.sha256(raw).hexdigest()
    if manifest.get("results_sha256") != sha:
        raise SystemExit("results.json does not match its manifest — not the file the run wrote")
    leak_p = d / "leak_checks.json"
    leak = json.loads(leak_p.read_text(encoding="utf-8")) if leak_p.exists() else None
    out = summarize(json.loads(raw), manifest, sha, leak, a.run_id)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    print(json.dumps({"written": a.out, "results_sha256": sha}))


if __name__ == "__main__":
    main()
