"""Why the backtest has no macro snapshot at t: the app's own macro request against the replayed FRED answers.

    python -m marketlens.backtest.why_macro --db backtest.db --t 2023-02-17

Prints the snapshot's error, every series the replay served or missed, and the recorded series ids."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime

from sqlalchemy import text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--t", required=True, help="an analysis day (YYYY-MM-DD); 20:00 New York")
    a = ap.parse_args(argv)

    from marketlens.application.data_access import DataAccess, TTLCache
    from marketlens.backtest.engine import backtest_config
    from marketlens.backtest.offline import build_offline_registry, no_network
    from marketlens.backtest.schema import bt_engine
    from marketlens.backtest.store import BacktestData, BacktestStore
    from marketlens.domain.macro import ALL_SERIES
    from marketlens.domain.market_calendar import NY
    from marketlens.infrastructure.db.session import make_session_factory

    eng = bt_engine(a.db)
    t = datetime.combine(date.fromisoformat(a.t), datetime.min.time().replace(hour=20), tzinfo=NY)
    with no_network():
        data = BacktestData(eng, make_session_factory(eng))
        store = BacktestStore(data)
        store.set_time(t)
        reg, replay, _blocked = build_offline_registry(eng, store)
        cfg = backtest_config()
        da = DataAccess(reg, cfg.cache_ttl, cache=TTLCache(clock=lambda: 0.0), store=store, now_fn=lambda: t)
        snap, err = da.macro_snapshot(t)
    with eng.connect() as c:
        recorded = [r[0] for r in c.execute(text("SELECT key FROM bt_http LIMIT 200000"))]
    ids = sorted({k.split("series_id=")[1].split("&")[0] for k in recorded if "series_id=" in k})
    print(json.dumps({
        "t": t.isoformat(), "snapshot": sorted(snap.series) if snap else None, "error": err,
        "asked": list(ALL_SERIES), "served": [u[:220] for u in replay.urls], "missed": [u[:220] for u in replay.misses],
        "recorded_series_ids": ids, "recorded_requests": len(recorded),
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
