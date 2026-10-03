"""A run cut short (a lost runner, a time budget) goes on from its checkpoint in another process and ends with exactly
the results.json of an uninterrupted run (the 7-year run on 2026-09-29 lost its runner after 62 minutes)."""

from __future__ import annotations

import json
import os

import pytest

from tests.backtest.test_engine_leaks import world  # noqa: F401


def test_a_resumed_run_gives_the_same_results(world, tmp_path):  # noqa: F811
    from marketlens.backtest.run import CHECKPOINT, main

    path, _eng, _data = world
    args = ["--db", path, "--start", "2026-03-02", "--end", "2026-06-26", "--min-names", "2", "--leak-checks", "0", "--workers", "1"]
    whole = main(args + ["--out", str(tmp_path / "whole")])

    cut = str(tmp_path / "cut")
    first = main(args + ["--out", cut, "--max-weeks", "3"])  # the first runner stops after three weeks
    assert first["done"] is False and first["weeks_done"] == 3 and os.path.exists(os.path.join(cut, CHECKPOINT))
    second = main(args + ["--out", cut, "--max-weeks", "4"])
    assert second["done"] is False and second["weeks_done"] == 7
    last = main(args + ["--out", cut])  # the rest
    assert last["results_sha256"] == whole["results_sha256"] and last["weeks"] == whole["weeks"]
    assert json.loads((tmp_path / "cut" / "status.json").read_text())["done"] is True
    assert not os.path.exists(os.path.join(cut, CHECKPOINT))


def test_a_checkpoint_of_another_run_is_refused(world, tmp_path):  # noqa: F811
    from marketlens.backtest.run import main

    path, _eng, _data = world
    args = ["--db", path, "--min-names", "2", "--leak-checks", "0", "--workers", "1", "--out", str(tmp_path / "o")]
    main(args + ["--start", "2026-03-02", "--end", "2026-06-26", "--max-weeks", "2"])
    with pytest.raises(SystemExit, match="another run"):
        main(args + ["--start", "2026-03-09", "--end", "2026-06-26"])  # another window


def test_fresh_workers_every_week_give_the_same_results(world, tmp_path):  # noqa: F811
    """Workers started again every week (--recycle-weeks, and at once on low memory) change nothing in the results."""
    from marketlens.backtest.run import main

    path, _eng, _data = world
    args = ["--db", path, "--start", "2026-03-02", "--end", "2026-06-26", "--min-names", "2", "--leak-checks", "0"]
    one = main(args + ["--out", str(tmp_path / "one"), "--workers", "1"])
    many = main(args + ["--out", str(tmp_path / "many"), "--workers", "2", "--recycle-weeks", "1"])
    assert many["results_sha256"] == one["results_sha256"]


# ------------------------------------------------------------------------------------------ the final phase in legs
def _spy(data):  # noqa: ANN001, ANN202
    return next((ln for ln in data.lineages if ln.labels and ln.labels[-1] == "SPY" and ln.cik is None), None)


def _until_done(f):  # noqa: ANN001, ANN202
    """Call ``f`` as the next leg would until it finishes; how many legs it took and the result."""
    from marketlens.backtest.run import OutOfTime

    for legs in range(1, 200):
        try:
            return legs, f()
        except OutOfTime:
            continue
    raise AssertionError("never finished")


def test_a_holdout_rerun_cut_into_legs_ends_as_one_uninterrupted(world, tmp_path, monkeypatch):  # noqa: F811
    """The 7-year run's holdout reruns (~150 weeks each, up to three) do not fit one runner: each leg goes on from the
    engine state the previous one saved, and the rerun ends with the state and statistics of one uninterrupted rerun."""
    import pickle
    from datetime import date

    import marketlens.backtest.run as R
    from marketlens.backtest.identity import weekly_times
    from marketlens.config import load_model_config

    _p, eng, data = world
    times = [t.isoformat() for t in weekly_times(date(2026, 3, 2), date(2026, 6, 26))]
    w = dict(load_model_config().scoring_model.weights)
    states: list[tuple] = []
    real = R._holdout_stats
    monkeypatch.setattr(R, "_holdout_stats", lambda e, *a: states.append(pickle.loads(pickle.dumps((e.state.later, e.state.previous, e.state.signals)))) or real(e, *a))  # noqa: S301
    whole = R.holdout_strategy(data, eng, w, times, times[4], 1, _spy(data))
    out = str(tmp_path)
    legs, cut = _until_done(lambda: R.holdout_strategy(data, eng, w, times, times[4], 1, _spy(data), out=out, deadline=0.0))  # one week a leg
    assert legs == len(times)
    assert states[0] == states[1]  # every analysis of every week, carried over the legs
    assert json.dumps(cut, sort_keys=True) == json.dumps(whole, sort_keys=True)
    kept = [f for f in os.listdir(out) if f.startswith("holdout-")]
    assert len(kept) == 1 and kept[0].endswith(".json")  # the week-by-week state is removed once the statistics are kept
    monkeypatch.setattr(R, "Engine", None, raising=False)  # a later leg reads the kept statistics, it does not run again
    assert R.holdout_strategy(data, eng, w, times, times[4], 1, _spy(data), out=out, deadline=0.0) == cut


def test_the_leak_checks_cut_into_legs_end_as_one_uninterrupted(world, tmp_path):  # noqa: F811
    from datetime import date

    from marketlens.backtest.engine import backtest_config
    from marketlens.backtest.identity import weekly_times
    from marketlens.backtest.run import leak_checks

    _p, eng, data = world
    times = weekly_times(date(2026, 3, 2), date(2026, 6, 26))
    whole = leak_checks(data, eng, backtest_config(), times, 2)
    legs, cut = _until_done(lambda: leak_checks(data, eng, backtest_config(), times, 2, str(tmp_path), None, 0.0))
    assert legs == 3  # each leg finishes one part: two truncated copies, then the canary
    assert json.dumps(cut, sort_keys=True) == json.dumps(whole, sort_keys=True)
    assert whole["truncated_all_equal"] is True and whole["canary"]


def test_the_whole_run_in_one_minute_legs_gives_the_same_results(world, tmp_path):  # noqa: F811
    """What the workflow does: the same command again and again with a time budget (here: past at once, so every leg
    does one week or one part of the final phase), --out carried from leg to leg — the results.json and the leak checks
    of one uninterrupted run."""
    from marketlens.backtest.run import main

    path, _eng, _data = world
    args = ["--db", path, "--start", "2026-03-02", "--end", "2026-06-26", "--min-names", "2", "--leak-checks", "2", "--workers", "1"]
    whole = main(args + ["--out", str(tmp_path / "whole")])
    cut = str(tmp_path / "cut")
    statuses = []
    for _ in range(60):
        st = main(args + ["--out", cut, "--budget-minutes", "0.000001"])
        statuses.append(st)
        if st.get("results_sha256"):
            break
    assert statuses[-1]["results_sha256"] == whole["results_sha256"]
    assert [s.get("final_phase") for s in statuses if s.get("final_phase")], "the final phase was cut too"
    assert all(s["weeks_done"] == s["weeks_total"] for s in statuses if s.get("final_phase"))
    assert (tmp_path / "cut" / "leak_checks.json").read_text() == (tmp_path / "whole" / "leak_checks.json").read_text()
    assert not os.path.exists(os.path.join(cut, "checkpoint.pkl"))


def test_rows_written_after_the_last_checkpoint_are_done_again(world, tmp_path):  # noqa: F811
    """A runner lost between saving a week's rows and its checkpoint: the next one runs that week again (no duplicate
    rows, no failure on the rows already there)."""
    import pickle

    from marketlens.backtest.run import CHECKPOINT, main

    path, _eng, _data = world
    args = ["--db", path, "--start", "2026-03-02", "--end", "2026-06-26", "--min-names", "2", "--leak-checks", "0", "--workers", "1"]
    whole = main(args + ["--out", str(tmp_path / "whole")])
    cut = str(tmp_path / "cut")
    main(args + ["--out", cut, "--max-weeks", "3"])
    before = (tmp_path / "cut" / CHECKPOINT).read_bytes()
    main(args + ["--out", cut, "--max-weeks", "1"])  # week 4's rows are saved …
    (tmp_path / "cut" / CHECKPOINT).write_bytes(before)  # … but the runner was lost before its checkpoint
    assert len(pickle.loads(before)["weeks"]) == 3  # noqa: S301
    last = main(args + ["--out", cut])
    assert last["results_sha256"] == whole["results_sha256"]
