"""The week's per-name analyses in forked workers give exactly the rows of the single-process run (same order, same
bytes), and the workers keep the network block and the time audit."""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import select

from marketlens.backtest.engine import Engine, backtest_config, bt_rows
from marketlens.backtest.identity import weekly_times
from marketlens.backtest.offline import build_offline_registry, no_network
from marketlens.backtest.schema import bt_engine
from marketlens.backtest.store import BacktestData, BacktestStore
from tests.backtest import bt_fixture as FX


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    from marketlens.infrastructure.db.session import make_session_factory

    path = str(tmp_path_factory.mktemp("btp") / "fx.db")
    FX.build(path)
    eng = bt_engine(path)
    return eng, BacktestData(eng, make_session_factory(eng))


def _run(world, tmp_path, name, workers):
    eng, data = world
    store = BacktestStore(data)
    reg, replay, blocked = build_offline_registry(eng, store)
    e = Engine(store, reg, backtest_config(), str(tmp_path / f"{name}.db"), replay=replay)
    e.blocked = blocked
    with no_network() as refused:
        e.start_workers(workers)
        e.min_parallel = 1  # the fixture has three names: parallelise anyway
        try:
            e.run(weekly_times(date(2026, 3, 2), date(2026, 5, 29)), log=lambda _m: None)
        finally:
            e.stop_workers()
    with e.out.connect() as c:
        rows = [tuple(r) for r in c.execute(select(bt_rows).order_by(bt_rows.c.t, bt_rows.c.key))]
    sigs = e.signals_json()
    return rows, sigs, e, refused


def test_workers_give_the_same_rows(world, tmp_path):
    one, s1, e1, _ = _run(world, tmp_path, "one", 1)
    two, s2, e2, refused = _run(world, tmp_path, "two", 3)
    assert e2.workers == 3 and e1.workers == 1
    assert len(one) > 20 and one == two
    assert s1 == s2
    assert e1.audited == e2.audited
    assert refused == []  # nothing tried to leave, in the parent or the workers
    assert e2.worker_counts["blocked"] > 0  # the workers asked the (blocked) providers like the parent does
