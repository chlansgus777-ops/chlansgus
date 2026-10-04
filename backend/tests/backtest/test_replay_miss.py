"""A request the collection never recorded is that one input missing at t, not the whole request: the SP500
vintages were never recorded (FRED has none before 2026), and the miss made the app drop every macro series — the
7-year run had no macro in any week (2026-10-04)."""

from __future__ import annotations

import shutil
from datetime import datetime

from sqlalchemy import text

from marketlens.domain.market_calendar import NY
from tests.backtest.test_engine_leaks import world  # noqa: F401


def test_an_unrecorded_series_leaves_the_rest_of_the_macro_picture(world, tmp_path):  # noqa: F811
    from marketlens.application.data_access import DataAccess, TTLCache
    from marketlens.backtest.engine import backtest_config
    from marketlens.backtest.offline import build_offline_registry
    from marketlens.backtest.schema import bt_engine
    from marketlens.backtest.store import BacktestData, BacktestStore
    from marketlens.domain.macro import SPX, US10Y
    from marketlens.infrastructure.db.session import make_session_factory

    path, _eng, _data = world
    copy = str(tmp_path / "no_sp500.db")
    shutil.copy(path, copy)
    eng = bt_engine(copy)
    with eng.begin() as c:
        assert c.execute(text("DELETE FROM bt_http WHERE url LIKE '%series_id=SP500%'")).rowcount > 0
    data = BacktestData(eng, make_session_factory(eng))
    store = BacktestStore(data)
    t = datetime(2026, 3, 6, 20, tzinfo=NY)
    store.set_time(t)
    reg, replay, _blocked = build_offline_registry(eng, store)
    da = DataAccess(reg, backtest_config().cache_ttl, cache=TTLCache(clock=lambda: 0.0), store=store, now_fn=lambda: t)
    snap, err = da.macro_snapshot(t)
    assert err is None and snap is not None
    assert US10Y in snap.series and SPX not in snap.series
    assert any("SP500" in u for u in replay.misses)  # still counted (the manifest's fred_replay_misses)
