"""PREREGISTRATION §12 / §13 rules (backtest/apply.py) — every branch on synthetic training verdicts and holdout rows."""

from __future__ import annotations

import random

import pytest

from marketlens.backtest import apply as A

OP = {"fundamental": 25.0, "valuation": 15.0, "earnings_revision": 15.0, "catalyst": 10.0, "macro": 10.0, "technical": 10.0,
      "risk": 10.0, "entry_rr": 15.0, "return_signals": 0.0}


def el(verdict: str, ic: float, p: float = 0.2) -> dict:
    return {"verdict": verdict, "mean_ic": ic, "holm_p": p}


def test_data_verdict_and_periods():
    assert A.data_verdict(104) == {"weeks": 104, "verdict": "부족", "adjust_allowed": False, "prefix": "예비: "}
    assert A.data_verdict(220)["prefix"] == "" and not A.data_verdict(373)["adjust_allowed"] and A.data_verdict(374)["adjust_allowed"]
    ts = [f"2020-{i:03d}" for i in range(100)]
    tr, gap, ho = A.split(ts)
    assert (len(tr), len(gap), len(ho)) == (70, 12, 18) and tr[-1] < gap[0] and gap[-1] < ho[0]


def test_no_effect_minus_5_negative_minus_10_valid_gets_ic_share_capped():
    train = {"component.fundamental": el("유효", 0.04), "component.valuation": el("유효", 0.01),
             "component.macro": el("효과 없음", 0.001), "component.technical": el("약함", -0.03, 0.01),
             "component.risk": el("약함", 0.01), "component.entry_rr": el("효과 없음", -0.01, 0.5)}
    w, log = A.adjust_weights(OP, train)
    assert w["macro"] == 5 and w["technical"] == 0 and w["entry_rr"] == 10  # −5, −10, −5
    # freed 5 + 10 + 5 = 20: fundamental 20×0.8 = 16 → capped +5; valuation 20×0.2 = +4; 11 left for nobody
    assert w["fundamental"] == 30 and w["valuation"] == 19 and w["risk"] == 10
    assert w["earnings_revision"] == 15 and w["catalyst"] == 10 and w["return_signals"] == 0  # never touched
    assert sum(w.values()) == sum(OP.values()) - 11  # normalised by the score's weighted mean, not re-spread
    assert any("남은" in x for x in log)


def test_weight_never_below_zero_and_points_nobody_takes_are_normalised_away():
    op = dict(OP, macro=3.0)
    w, log = A.adjust_weights(op, {"component.macro": el("약함", -0.02, 0.001)})
    assert "합계 정규화" in log[-1]
    assert w["macro"] == 0  # 3 − min(10, 3)
    same, log = A.adjust_weights(OP, {f"component.{c}": el("약함", 0.01) for c in A.VERIFIABLE})
    assert same == OP and log == []


def test_signals_candidate_only_when_valid():
    assert A.signals_candidate(OP, {"component.return_signals": el("약함", 0.03)})[0] is None
    assert A.signals_candidate(OP, {})[0] is None
    w, why = A.signals_candidate(OP, {"component.return_signals": el("유효", 0.03)})
    assert w["return_signals"] == 10 and abs(sum(w.values()) - 110) < 1e-3 and "10점" in why
    assert w["fundamental"] == pytest.approx(25 * 100 / 110, abs=1e-3)


def _row(subs: dict, weights: dict, ret: float, adv: float = 5e7) -> dict:
    comps = {c: {"sub": s, "available": True, "coverage": 1.0} for c, s in subs.items()}
    p = {"components": comps}
    p["total"] = A.rescore(p, weights)
    return {"p": p, "ret": {0.0: ret, 0.3: ret, 0.55: ret}, "adv": adv}


def _weeks(signal_weight: float, n_weeks: int = 30, n: int = 150, seed: int = 7) -> list[list[dict]]:
    """Returns follow return_signals (+ noise); the other components are noise."""
    rng = random.Random(seed)
    bt = A.backtest_weights(OP)
    out = []
    for _ in range(n_weeks):
        cs = []
        for _i in range(n):
            subs = {c: rng.random() for c in OP}
            ret = signal_weight * (subs["return_signals"] - 0.5) + rng.gauss(0, 0.05)
            cs.append(_row(subs, bt, ret))
        out.append(cs)
    return out


def test_rescoring_reproduces_the_stored_totals_and_measures_other_weights():
    weeks = _weeks(0.3)
    assert A.stored_totals_reproduced(weeks, A.backtest_weights(OP))
    old = A.score_quality(weeks, A.backtest_weights(OP))
    cand = A.score_quality(weeks, A.backtest_weights(dict(OP, return_signals=10.0)))
    assert cand["mean_ic"] > old["mean_ic"] and cand["top_minus_bottom"] > old["top_minus_bottom"]


def test_adoption_needs_all_three_conditions():
    new, old = {"mean_ic": 0.03, "top_minus_bottom": 0.02}, {"mean_ic": 0.02, "top_minus_bottom": 0.01}
    assert A.adopt(new, old, -0.20, -0.19)["adopted"]
    assert not A.adopt(new, old, -0.22, -0.19)["adopted"]  # drawdown 3 pp worse
    assert not A.adopt({"mean_ic": -0.01, "top_minus_bottom": 0.02}, {"mean_ic": -0.02, "top_minus_bottom": 0.01}, -0.1, -0.1)["adopted"]  # IC ≤ 0
    assert not A.adopt({"mean_ic": 0.03, "top_minus_bottom": 0.0}, old, -0.1, -0.1)["adopted"]
    assert not A.adopt(new, old, None, -0.1)["adopted"]


def test_apply_rules_end_to_end_adopts_the_signals_only_on_the_holdout_evidence():
    train = {f"component.{c}": el("약함", 0.01) for c in A.VERIFIABLE} | {"component.return_signals": el("유효", 0.05, 0.001)}
    verdict = A.data_verdict(500)
    calls = []

    def mdd(w):
        calls.append(dict(w))
        return -0.15

    good = A.apply_rules(OP, verdict, train, _weeks(0.3), mdd)
    assert good["s12"]["adopted"] is False and good["s13"]["adopted"] is True and good["final_weights"]["return_signals"] == 10
    assert good["changed"] and len(calls) == 2  # the candidate and its comparison, each re-run once
    flat = A.apply_rules(OP, verdict, train, _weeks(-0.3), lambda _w: -0.15)  # the holdout contradicts the training
    assert flat["s13"]["adopted"] is False and flat["final_weights"] == OP and not flat["changed"]


def test_too_little_data_changes_nothing():
    out = A.apply_rules(OP, A.data_verdict(300), {"component.return_signals": el("유효", 0.05)}, [], lambda _w: -0.1)
    assert out["final_weights"] == OP and out["s12"]["adopted"] is False and out["s13"]["adopted"] is False


def test_a_rescoring_that_does_not_reproduce_the_stored_totals_stops_the_run():
    weeks = _weeks(0.1, n_weeks=2)
    weeks[0][0]["p"]["total"] += 5
    with pytest.raises(RuntimeError):
        A.apply_rules(OP, A.data_verdict(500), {}, weeks, lambda _w: -0.1)
