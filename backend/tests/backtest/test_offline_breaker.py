"""The backtest's providers answer from the stored point-in-time data: names without a quote at t never close the
chain for the names after them. The default circuit breaker opened after five such misses for 60 s of wall-clock
time, so which names lost their price depended on the runner's speed and where the legs resumed — the 7-year run
and its reproduction differed in entry R/R coverage (2026-10-04)."""

from __future__ import annotations

from datetime import datetime

import pytest

from marketlens.domain.market_calendar import NY
from tests.backtest.test_engine_leaks import world  # noqa: F401


def test_quotes_missing_at_t_never_block_the_next_names(world):  # noqa: F811
    from marketlens.backtest.offline import build_offline_registry
    from marketlens.backtest.store import BacktestStore
    from marketlens.providers.router import AllProvidersFailed

    _path, eng, data = world
    store = BacktestStore(data)
    store.set_time(datetime(2026, 3, 6, 20, tzinfo=NY))
    reg, _replay, _blocked = build_offline_registry(eng, store)
    chain = reg.chain("price")
    for _ in range(20):  # GONE stopped trading in 2025: no quote at t, every time
        with pytest.raises(AllProvidersFailed):
            chain.call("get_quote", "GONE")
    assert chain.call("get_quote", "AAA").value.price > 0  # the breaker never opened
