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
