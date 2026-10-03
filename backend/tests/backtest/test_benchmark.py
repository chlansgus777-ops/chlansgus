"""The benchmark is the security that traded as SPY, whether or not its reference record carries a CIK. The first
7-year run (2026-10-03) looked for "the SPY without a CIK"; the real records give the SPDR trust CIK 884394, so it
found none: 0 weeks of data (the §12 verdict "부족", adjustments not allowed) and no cross-section measured at all —
each one is skipped without the benchmark's return over the same horizon."""

from __future__ import annotations

import json

import pytest

from marketlens.backtest.apply import data_weeks
from marketlens.backtest.schema import bt_engine
from marketlens.backtest.store import BacktestData
from tests.backtest import bt_fixture as FX


@pytest.fixture(scope="module", params=[None, 884394], ids=["no-cik", "spdr-cik"])
def built(request, tmp_path_factory):  # noqa: ANN001, ANN201
    from marketlens.infrastructure.db.session import make_session_factory

    path = str(tmp_path_factory.mktemp("bm") / "fx.db")
    FX.build(path, spy_cik=request.param)
    eng = bt_engine(path)
    return path, BacktestData(eng, make_session_factory(eng))


def test_the_benchmark_is_found_with_or_without_a_cik(built):  # noqa: ANN001
    _path, data = built
    spy = data.benchmark()
    assert spy is not None and set(spy.labels) == {"SPY"}
    assert spy.days == FX.sessions()
    assert data_weeks(spy.days) >= 70


def test_the_run_measures_against_it(built, tmp_path):  # noqa: ANN001
    from marketlens.backtest.run import main

    path, _data = built
    out = tmp_path / "o"
    main(["--db", path, "--out", str(out), "--start", "2026-03-02", "--end", "2026-06-26", "--min-names", "2", "--leak-checks", "0", "--workers", "1"])
    res = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert res["application"]["data"]["weeks"] >= 70
    assert res["horizons"]["20"]["weeks_used"] > 0  # every cross-section is measured against SPY's return: none without it
