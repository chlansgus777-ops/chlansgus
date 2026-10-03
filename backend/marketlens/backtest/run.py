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
import hashlib
import json
import logging
import os
import pickle
import random
import subprocess
import time
from datetime import date, datetime, timezone
from typing import Any

from marketlens.backtest.identity import weekly_times
from marketlens.backtest.schema import bt_engine, file_sha256

WARMUP_SESSIONS = 252
SEED = 20260928
CHECKPOINT = "checkpoint.pkl"  # in --out: the weeks done so far and the engine's state after them (resumable)


class OutOfTime(Exception):
    """The time budget ran out in the final phase (the holdout reruns, the leak checks): what is finished is kept in
    --out (``_cached``) and the same command goes on from there."""


def _cached(out: str | None, name: str) -> Any:
    """A finished part of the final phase that an earlier process left in --out, or None."""
    if out is None or not os.path.exists(os.path.join(out, name)):
        return None
    with open(os.path.join(out, name), encoding="utf-8") as f:
        return json.load(f)


def _keep(out: str | None, name: str, value: Any) -> None:
    if out is None:
        return
    tmp = os.path.join(out, name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, sort_keys=True)
    os.replace(tmp, os.path.join(out, name))


def _part_name(prefix: str, ident: dict[str, Any] | None, **what: Any) -> str:
    """A file name for one part of the final phase of this run (data, config and window: ``ident``)."""
    blob = json.dumps({"ident": ident, **what}, sort_keys=True, default=str).encode()
    return f"{prefix}-{hashlib.sha256(blob).hexdigest()[:16]}"


def _unfinished(out: str, status: dict[str, Any]) -> dict[str, Any]:
    """Where a stopped process left the run (status.json in --out: the workflow's next leg reads it)."""
    with open(os.path.join(out, "status.json"), "w", encoding="utf-8") as fh:
        json.dump(status, fh)
    print(json.dumps(status))
    return status


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
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 1, help="processes for the per-name analyses of a week (same rows as 1)")
    ap.add_argument("--budget-minutes", type=float, default=0.0,
                    help="stop after the week that passes this many minutes, with a checkpoint in --out; the same command "
                         "again (on another machine, with --out copied) goes on from there. 0 = no limit")
    ap.add_argument("--max-weeks", type=int, default=0, help="stop after this many weeks in this process (tests); 0 = no limit")
    ap.add_argument("--recycle-weeks", type=int, default=0, help="fresh workers every this many weeks (0 = the engine's default; same rows)")
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
        # a checkpoint of an earlier process (another runner) with the same data, config and window: go on from it
        cp_path = os.path.join(a.out, CHECKPOINT)
        ident = {"data_sha256": data_hash, "config": config_fingerprint(cfg), "times": [t.isoformat() for t in times], "version": 1}
        cp = None
        if os.path.exists(cp_path):
            with open(cp_path, "rb") as f:
                cp = pickle.load(f)  # noqa: S301 - our own file, written by this function in --out
            if cp["ident"] != ident:
                raise SystemExit(f"{cp_path}: a checkpoint of another run (data, config or window differ) — remove it or use another --out")
        engine = Engine(store, reg, cfg, os.path.join(a.out, "rows.db"), replay=replay, resume=cp is not None)
        engine.blocked = blocked
        if a.recycle_weeks:
            engine.RECYCLE_WEEKS = a.recycle_weeks
        done: list[dict[str, Any]] = list(cp["weeks"]) if cp else []
        elapsed0 = cp["elapsed"] if cp else 0.0
        if cp:
            engine.restore(cp["engine"])
            if len(done) < len(times):  # rows a stopped runner wrote after its last checkpoint: that week runs again
                from sqlalchemy import delete

                from marketlens.backtest.engine import bt_rows

                with engine.out.begin() as c:
                    c.execute(delete(bt_rows).where(bt_rows.c.t >= times[len(done)].isoformat()))
            print(json.dumps({"resumed": len(done), "of": len(times)}))

        def save(info: dict[str, Any]) -> None:  # after every week: a lost runner loses at most the week it was in
            done.append(info)
            tmp = cp_path + ".tmp"
            with open(tmp, "wb") as f:
                pickle.dump({"ident": ident, "weeks": done, "engine": engine.checkpoint(), "elapsed": elapsed0 + time.monotonic() - t_start}, f,
                            protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, cp_path)

        todo = times[len(done):]
        if a.max_weeks:
            todo = todo[: a.max_weeks]
        deadline = t_start + a.budget_minutes * 60 if a.budget_minutes else None
        engine.start_workers(a.workers)
        try:
            engine.run(todo, deadline=deadline, on_week=save)
        finally:
            engine.stop_workers()
        weeks = done
        if len(weeks) < len(times):
            return _unfinished(a.out, {"done": False, "weeks_done": len(weeks), "weeks_total": len(times), "next": times[len(weeks)].isoformat()})
        from marketlens.backtest.apply import data_verdict, data_weeks

        spy_ln = data.benchmark()
        verdict = data_verdict(data_weeks(spy_ln.days if spy_ln else []))
        # the final phase (holdout reruns, leak checks) runs under the same budget: what is finished stays in --out and
        # the next process goes on from there (the 7-year run: three reruns of ~150 weeks would not fit one runner)
        try:
            results = build(engine.out, data, eng, (engine.state.signals, engine.state.later, engine._splits, store), cfg.paper, a.min_names, SEED,
                            prefix=verdict["prefix"])
            results["application"] = application(data, eng, engine, [w["t"] for w in weeks], verdict, a, spy_ln, ident, deadline)
            leak = leak_checks(data, eng, cfg, times, a.leak_checks, a.out, ident, deadline) if a.leak_checks else {"skipped": True}
        except OutOfTime as e:
            return _unfinished(a.out, {"done": False, "weeks_done": len(weeks), "weeks_total": len(times), "final_phase": str(e)})
    with open(os.path.join(a.out, "leak_checks.json"), "w", encoding="utf-8") as f:  # outside results.json: a reproduction
        json.dump(leak, f, indent=2, sort_keys=True)                                    # may skip the (slow) checks
    results["run"] = {"weeks": [w["t"] for w in weeks], "eligible_per_week": [w["eligible"] for w in weeks],
                      "window": [start.isoformat(), end.isoformat()], "config": config_fingerprint(cfg), "data_sha256": data_hash,
                      "seed": SEED, "min_names": a.min_names}
    digest = dump(results, os.path.join(a.out, "results.json"))
    manifest = {
        "commit": git_commit(), "config_fingerprint": config_fingerprint(cfg), "scoring_version": cfg.scoring_model.version,
        "weights": dict(cfg.scoring_model.weights), "min_completeness": cfg.decision.min_completeness, "backtest_raw": cfg.raw.get("backtest"),
        "data_sha256": data_hash, "results_sha256": digest, "seed": SEED, "runtime_seconds": round(elapsed0 + time.monotonic() - t_start, 1),
        "weeks": len(weeks), "audited_inputs": engine.audited, "fred_replayed": replay.served + engine.worker_counts["replay_served"], "workers": engine.workers, "fred_replay_misses": len(replay.misses),
        "blocked_provider_attempts": {k: b.attempts for k, b in blocked.items()}, "blocked_attempts_in_workers": engine.worker_counts["blocked"], "sockets_refused": len(refused),
        "network_isolation": os.environ.get("BACKTEST_NETNS", "socket-guard-only"),
        "leak_checks": {"truncated_all_equal": leak.get("truncated_all_equal"), "canary": leak.get("canary")} if not leak.get("skipped") else "skipped",
        "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    with open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, sort_keys=True)
    with open(os.path.join(a.out, "status.json"), "w", encoding="utf-8") as f:
        json.dump({"done": True, "weeks_done": len(weeks), "weeks_total": len(times)}, f)
    if os.path.exists(os.path.join(a.out, CHECKPOINT)):
        os.remove(os.path.join(a.out, CHECKPOINT))  # the run is complete: results.json and rows.db are what it produced
    print(json.dumps({"results_sha256": digest, "weeks": len(weeks), "runtime_seconds": manifest["runtime_seconds"]}))
    return manifest


def application(data: Any, eng: Any, engine: Any, times: list[str], verdict: dict[str, Any], a: Any, spy_ln: Any,
                ident: dict[str, Any] | None = None, deadline: float | None = None) -> dict[str, Any]:
    """PREREGISTRATION §12 / §13 (backtest/apply.py): training verdicts, the proposals, the holdout adoption tests.
    Each holdout rerun is checkpointed in --out week by week and kept there when finished (``deadline``: OutOfTime)."""
    from marketlens.backtest.apply import apply_rules, holdout_cross_sections, split
    from marketlens.backtest.report import build
    from marketlens.config import load_model_config

    base = load_model_config()
    train, gap, hold = split(times)
    periods = {name: {"first": ts[0] if ts else None, "last": ts[-1] if ts else None, "weeks": len(ts)} for name, ts in (("train", train), ("gap", gap), ("holdout", hold))}
    if not verdict["adjust_allowed"] or not hold:
        out = apply_rules(base.scoring_model.weights, verdict, None, [], lambda _w: None)
        return out | {"periods": periods}
    train_res = build(engine.out, data, eng, None, base.paper, a.min_names, SEED, only_t=set(train), prefix=verdict["prefix"])
    train_h60 = train_res["horizons"]["60"]["elements"]
    hold_res = build(engine.out, data, eng, None, base.paper, a.min_names, SEED, only_t=set(hold), prefix=verdict["prefix"])
    weeks = holdout_cross_sections(engine.out, data, set(hold), a.min_names)
    reruns: dict[str, Any] = {}

    def strategy_mdd(w: Any) -> float | None:
        stats = holdout_strategy(data, eng, dict(w), gap + hold, hold[0], a.workers, spy_ln, out=a.out, ident=ident, deadline=deadline)
        reruns[json.dumps(sorted((k, float(v)) for k, v in w.items()))] = stats
        return stats.get("max_drawdown") if stats else None

    out = apply_rules(base.scoring_model.weights, verdict, train_h60, weeks, strategy_mdd)
    slim = lambda els: {k: {kk: vv for kk, vv in v.items() if kk != "weekly_ic"} for k, v in els.items()}  # noqa: E731
    return out | {"periods": periods, "train_h60": slim(train_h60), "holdout_h60": slim(hold_res["horizons"]["60"]["elements"]),
                  "holdout_strategy_reruns": reruns}


def holdout_strategy(data: Any, eng: Any, weights: dict[str, float], times: list[str], hold_start: str, workers: int, spy_ln: Any,
                     out: str | None = None, ident: dict[str, Any] | None = None, deadline: float | None = None) -> dict[str, Any]:
    """The app-rule strategy on the holdout with these weights' own decisions: a fresh engine from the gap's first week
    (the gap gives the hysteresis and the carried stops time to form), signals from the holdout's first week on.
    ``out``: the engine's state is saved there after every week and the finished statistics are kept, so a later process
    goes on where a ``deadline`` stopped this one (OutOfTime) — with the same statistics as one uninterrupted rerun."""
    from marketlens.backtest.engine import Engine, backtest_config
    from marketlens.backtest.offline import build_offline_registry
    from marketlens.backtest.store import BacktestStore
    from marketlens.config import load_model_config

    name = _part_name("holdout", ident, weights=sorted((k, float(v)) for k, v in weights.items()), times=list(times), hold_start=hold_start)
    kept = _cached(out, name + ".json")
    if kept is not None:
        return kept  # type: ignore[no-any-return]
    base = load_model_config()
    cfg = backtest_config(load_model_config(weights_override=weights, scoring_version_override=base.scoring_model.version + "+holdout-candidate"))
    store = BacktestStore(data)
    reg, replay, blocked = build_offline_registry(eng, store)
    e = Engine(store, reg, cfg, ":memory:", replay=replay)  # the reruns need only the engine's state, not its rows
    e.blocked = blocked
    ts = [datetime.fromisoformat(t) for t in times]
    cp_path = os.path.join(out, name + ".pkl") if out is not None else None
    n_done = 0
    if cp_path is not None and os.path.exists(cp_path):
        with open(cp_path, "rb") as f:
            cp = pickle.load(f)  # noqa: S301 - our own file, written below in --out
        e.restore(cp["engine"])
        n_done = cp["weeks"]

    def save(_info: dict[str, Any]) -> None:
        nonlocal n_done
        n_done += 1
        if cp_path is not None:
            with open(cp_path + ".tmp", "wb") as f:
                pickle.dump({"engine": e.checkpoint(), "weeks": n_done}, f, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(cp_path + ".tmp", cp_path)

    e.start_workers(workers)
    try:
        e.run(ts[n_done:], log=lambda _m: None, deadline=deadline, on_week=save)
    finally:
        e.stop_workers()
    if n_done < len(ts):
        raise OutOfTime(f"holdout rerun {name}: {n_done}/{len(ts)} weeks")
    # as an earlier process's kept statistics read back: an interrupted rerun and an uninterrupted one give the same values
    stats = json.loads(json.dumps(_holdout_stats(e, data, eng, cfg, store, hold_start, spy_ln), sort_keys=True))
    _keep(out, name + ".json", stats)
    if cp_path is not None and os.path.exists(cp_path):
        os.remove(cp_path)
    return stats


def _holdout_stats(e: Any, data: Any, eng: Any, cfg: Any, store: Any, hold_start: str, spy_ln: Any) -> dict[str, Any]:
    from marketlens.backtest.engine import FAR_FUTURE, final_basis_bars
    from marketlens.backtest.report import load_series
    from marketlens.backtest.store import total_return_path
    from marketlens.backtest.strategy import run_strategy

    start = datetime.fromisoformat(hold_start)
    sigs = [s for s in e.state.signals if s.t >= start]
    if not sigs:
        return {"signals": 0, "max_drawdown": 0.0}
    series = load_series(eng)
    spy_tr = {d: c for d, _o, c in total_return_path(spy_ln, spy_ln.days[0], spy_ln.days[-1])} if spy_ln else {}
    bars = {k: final_basis_bars(store, k, FAR_FUTURE) for k in sorted({s.key for s in sigs})}
    stats = run_strategy(sigs, e.state.later, e._splits, bars, cfg.paper, data.last_session, 1.0, 0.30, series.get("DTB3", []), spy_tr)
    stats.pop("equity_weekly", None)
    return {"signals": len(sigs), **stats}


def leak_checks(data: Any, eng: Any, cfg: Any, times: list[datetime], n: int, out: str | None = None, ident: dict[str, Any] | None = None,
                deadline: float | None = None) -> dict[str, Any]:
    """Checks 2 and 3 on the real data: truncated copies at ``n`` random weeks; a canary bar + filing right after t.
    ``out``: each finished week (and the canary) is kept there; past ``deadline`` the next one is left to a later process
    (OutOfTime) — at least one is done per process, so every process moves the checks on."""
    from marketlens.backtest.leaks import stateless_rows, truncated, with_canary

    rng = random.Random(SEED)
    picks = sorted(rng.sample(times, min(n, len(times))))
    did = 0

    def due(what: str) -> None:
        if did and deadline is not None and time.monotonic() >= deadline:
            raise OutOfTime(what)

    trunc = []
    for i, t in enumerate(picks):
        name = _part_name("leak-truncated", ident, t=t.isoformat()) + ".json"
        item = _cached(out, name)
        if item is None:
            due(f"leak checks: {i}/{len(picks)} truncated copies")
            full = stateless_rows(data, eng, cfg, t)
            cut = stateless_rows(truncated(data, t), eng, cfg, t)
            diff = sorted(k for k in set(full) | set(cut) if full.get(k) != cut.get(k))
            item = {"t": t.isoformat(), "rows": len(full), "differences": len(diff), "examples": diff[:5]}
            _keep(out, name, item)
            did += 1
        trunc.append(item)
    canary_name = _part_name("leak-canary", ident, n=n) + ".json"
    canary: dict[str, Any] | None = _cached(out, canary_name)
    if canary is None:
        due("leak checks: the canary")
        canary = {}
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
        _keep(out, canary_name, canary)
    return {"truncated_copy": trunc, "truncated_all_equal": all(x["differences"] == 0 for x in trunc), "canary": canary}


if __name__ == "__main__":
    main()
