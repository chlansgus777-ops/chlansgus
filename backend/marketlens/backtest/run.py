"""Run the backtest offline and write results.json + manifest.json (reproduction: the same command on the same database
gives the same results.json SHA-256).

    python -m marketlens.backtest.run --db backtest.db --out out/ [--start YYYY-MM-DD] [--end YYYY-MM-DD]
        [--min-names 100] [--leak-checks 20]

Defaults come from docs/backtest/PREREGISTRATION.md: the first analysis week is the first one with 252 completed
sessions of stored history; the last is the last week of the data (returns that have not matured are left out).
The whole run happens with every outbound socket refused (leak check 4).
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import random
import subprocess
import time
from datetime import date, datetime, timezone
from typing import Any

from marketlens.backtest.identity import weekly_times
from marketlens.backtest.schema import bt_engine, file_sha256

WARMUP_SESSIONS = 252
SEED = 20260928


def git_commit() -> str:
    if os.environ.get("GITHUB_SHA"):
        return os.environ["GITHUB_SHA"]
    try:
        return subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:  # noqa: BLE001 - reported as unknown
        return "unknown"


def default_window(data: Any) -> tuple[date, date]:
    from marketlens.domain.market_calendar import add_trading_days

    start = add_trading_days(data.first_session, WARMUP_SESSIONS)
    return start, data.last_session


def main(argv: list[str] | None = None) -> dict[str, Any]:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start")
    ap.add_argument("--end")
    ap.add_argument("--min-names", type=int, default=100)
    ap.add_argument("--leak-checks", type=int, default=20, help="random weeks for the truncated-copy check (0 = skip)")
    a = ap.parse_args(argv)
    logging.getLogger("marketlens").setLevel(logging.ERROR)  # provider failures are expected (blocked) — counted below
    os.makedirs(a.out, exist_ok=True)
    t_start = time.monotonic()

    from marketlens.backtest.engine import Engine, backtest_config, config_fingerprint
    from marketlens.backtest.offline import build_offline_registry, no_network
    from marketlens.backtest.report import build, dump
    from marketlens.backtest.store import BacktestData, BacktestStore
    from marketlens.infrastructure.db.session import make_session_factory

    data_hash = file_sha256(a.db)
    eng = bt_engine(a.db)
    cfg = backtest_config()
    with no_network() as refused:
        data = BacktestData(eng, make_session_factory(eng))
        start, end = default_window(data)
        start = date.fromisoformat(a.start) if a.start else start
        end = date.fromisoformat(a.end) if a.end else end
        times = weekly_times(start, end)
        store = BacktestStore(data)
        reg, replay, blocked = build_offline_registry(eng, store)
        engine = Engine(store, reg, cfg, os.path.join(a.out, "rows.db"), replay=replay)
        weeks = engine.run(times)
        results = build(engine.out, data, eng, (engine.state.signals, engine.state.later, engine._splits, store), cfg.paper, a.min_names, SEED)
        leak = leak_checks(data, eng, cfg, times, a.leak_checks) if a.leak_checks else {"skipped": True}
    with open(os.path.join(a.out, "leak_checks.json"), "w", encoding="utf-8") as f:  # outside results.json: a reproduction
        json.dump(leak, f, indent=2, sort_keys=True)                                    # may skip the (slow) checks
    results["run"] = {"weeks": [w["t"] for w in weeks], "eligible_per_week": [w["eligible"] for w in weeks],
                      "window": [start.isoformat(), end.isoformat()], "config": config_fingerprint(cfg), "data_sha256": data_hash,
                      "seed": SEED, "min_names": a.min_names}
    digest = dump(results, os.path.join(a.out, "results.json"))
    manifest = {
        "commit": git_commit(), "config_fingerprint": config_fingerprint(cfg), "scoring_version": cfg.scoring_model.version,
        "weights": dict(cfg.scoring_model.weights), "min_completeness": cfg.decision.min_completeness, "backtest_raw": cfg.raw.get("backtest"),
        "data_sha256": data_hash, "results_sha256": digest, "seed": SEED, "runtime_seconds": round(time.monotonic() - t_start, 1),
        "weeks": len(weeks), "audited_inputs": engine.audited, "fred_replayed": replay.served, "fred_replay_misses": len(replay.misses),
        "blocked_provider_attempts": {k: b.attempts for k, b in blocked.items()}, "sockets_refused": len(refused),
        "network_isolation": os.environ.get("BACKTEST_NETNS", "socket-guard-only"),
        "leak_checks": {"truncated_all_equal": leak.get("truncated_all_equal"), "canary": leak.get("canary")} if not leak.get("skipped") else "skipped",
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    print(json.dumps({"results_sha256": digest, "weeks": len(weeks), "runtime_seconds": manifest["runtime_seconds"]}))
    return manifest


def leak_checks(data: Any, eng: Any, cfg: Any, times: list[datetime], n: int) -> dict[str, Any]:
    """Checks 2 and 3 on the real data: truncated copies at ``n`` random weeks; a canary bar + filing right after t."""
    from marketlens.backtest.leaks import stateless_rows, truncated, with_canary

    rng = random.Random(SEED)
    picks = sorted(rng.sample(times, min(n, len(times))))
    trunc = []
    for t in picks:
        full = stateless_rows(data, eng, cfg, t)
        cut = stateless_rows(truncated(data, t), eng, cfg, t)
        diff = sorted(k for k in set(full) | set(cut) if full.get(k) != cut.get(k))
        trunc.append({"t": t.isoformat(), "rows": len(full), "differences": len(diff), "examples": diff[:5]})
    canary: dict[str, Any] = {}
    idx = [i for i in range(len(times) - 1)]
    if idx:
        i = rng.choice(idx)
        t, t_next = times[i], times[i + 1]
        base = stateless_rows(data, eng, cfg, t)
        eligible = sorted(k for k, v in base.items() if v.startswith("1|"))
        if eligible:
            key = rng.choice(eligible)
            fake = with_canary(data, key, t)
            at_t = stateless_rows(fake, eng, cfg, t)
            before_next = stateless_rows(data, eng, cfg, t_next)
            after_next = stateless_rows(fake, eng, cfg, t_next)
            canary = {"t": t.isoformat(), "next": t_next.isoformat(), "key": key,
                      "unchanged_at_t": at_t == base, "visible_after": before_next.get(key) != after_next.get(key)}
    return {"truncated_copy": trunc, "truncated_all_equal": all(x["differences"] == 0 for x in trunc), "canary": canary}


if __name__ == "__main__":
    main()
