"""Backtest stage 2–3 in CI (fixture database, no network): the engine on the app's own code, the leak checks 1–5,
identity / total return / delisting handling, the backtest-only configuration and the statistics."""

from __future__ import annotations

import json
import random
import socket
from dataclasses import replace
from datetime import date, datetime, timedelta

import pytest

from marketlens.backtest import leaks as L
from marketlens.backtest.engine import (BACKTEST_MIN_COMPLETENESS, Engine, Signal, backtest_config, final_basis_bars, half_spread)
from marketlens.backtest.identity import weekly_times
from marketlens.backtest.metrics import holm, newey_west_t, nw_lag, quintiles, ranks, spearman
from marketlens.backtest.offline import SocketBlocked, build_offline_registry, no_network
from marketlens.backtest.outcomes import outcome
from marketlens.backtest.schema import bt_engine
from marketlens.backtest.store import BacktestData, BacktestStore, total_return_path
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import NY
from tests.backtest import bt_fixture as FX

T = datetime(2026, 5, 29, 20, 0, tzinfo=NY)  # a Friday
T_NEXT = datetime(2026, 6, 5, 20, 0, tzinfo=NY)


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    from marketlens.infrastructure.db.session import make_session_factory

    path = str(tmp_path_factory.mktemp("bt") / "fx.db")
    FX.build(path)
    eng = bt_engine(path)
    return path, eng, BacktestData(eng, make_session_factory(eng))


def _gather(data, eng, t):
    from marketlens.application.data_access import DataAccess
    from marketlens.application.graph_seed import GraphSeed
    from marketlens.application.scanner import Scanner
    from marketlens.application.theses import ThesisBook

    cfg = backtest_config()
    store = BacktestStore(data)
    reg, _r, _b = build_offline_registry(eng, store)
    store.set_time(t)
    sc = Scanner(DataAccess(reg, cfg.cache_ttl, store=store, now_fn=lambda: t), cfg, GraphSeed(), ThesisBook())
    ctx = sc.build_context(t)
    return store, sc.gather_inputs(ctx, ctx.securities["AAA"], None, (), full=True)


# ------------------------------------------------------------------------------------------ 1. time audit
def test_time_audit_passes_real_inputs_and_fails_every_kind_of_later_input(world):
    _p, eng, data = world
    store, inp = _gather(data, eng, T)
    L.audit_inputs(inp, T, store)  # what the app gathered at t passes
    assert inp.quote is not None and inp.quote.timestamp <= T and inp.bars[-1].day == date(2026, 5, 29)
    later = Bar(date(2026, 6, 1), 1, 1, 1, 1, 1)
    q = inp.quarters[-1]
    bad = {
        "bar": replace(inp, bars=inp.bars + (later,)),
        "benchmark": replace(inp, benchmark_bars=inp.benchmark_bars + (later,)),
        "quote": replace(inp, quote=replace(inp.quote, timestamp=T + timedelta(minutes=1))),
        "filing": replace(inp, quarters=inp.quarters + (replace(q, period_end=date(2026, 6, 30), filed_date=date(2026, 5, 30)),)),
        "field": replace(inp, quarters=inp.quarters[:-1] + (replace(q, field_filed={"revenue": date(2026, 6, 2)}),)),
        "restatement": replace(inp, quarters=inp.quarters[:-1] + (replace(q, revisions={"revenue": ((date(2026, 6, 2), 1.0),)}),)),
        "split": replace(inp, splits=inp.splits + (replace(inp.splits[0], execution_date=date(2026, 6, 1)),)),
        "analyst": replace(inp, analyst=object()),
        "events": replace(inp, events=(object(),)),
        "extras": replace(inp, extras={"cet1": 0.12}),
    }
    for name, x in bad.items():
        with pytest.raises(L.LeakError):
            L.audit_inputs(x, T, store)
        assert name  # each kind fails on its own


def test_fred_vintages_after_t_fail_the_audit():
    ok = ["api.stlouisfed.org/fred/series/observations?observation_end=2026-05-29&realtime_end=2026-05-29&realtime_start=2026-05-29&series_id=DGS10"]
    L.audit_vintages(ok, T)
    with pytest.raises(L.LeakError):
        L.audit_vintages([ok[0].replace("realtime_end=2026-05-29", "realtime_end=2026-05-30")], T)


# ------------------------------------------------------------------------------------------ 2. truncated copy
def test_truncated_copy_gives_the_same_analysis(world):
    _p, eng, data = world
    cfg = backtest_config()
    for t in (datetime(2026, 2, 27, 20, tzinfo=NY), T):
        full = L.stateless_rows(data, eng, cfg, t)
        cut = L.stateless_rows(L.truncated(data, t), eng, cfg, t)
        assert full and full == cut


# ------------------------------------------------------------------------------------------ 3. canary
def test_canary_after_t_changes_nothing_at_t_and_shows_once_public(world):
    _p, eng, data = world
    cfg = backtest_config()
    key = data.by_key and next(k for k in data.by_key if k.startswith("AAA"))
    fake = L.with_canary(data, key, T)
    assert L.stateless_rows(fake, eng, cfg, T) == L.stateless_rows(data, eng, cfg, T)
    after_real, after_fake = L.stateless_rows(data, eng, cfg, T_NEXT), L.stateless_rows(fake, eng, cfg, T_NEXT)
    assert after_real[key] != after_fake[key]
    p_real, p_fake = json.loads(after_real[key].split("|", 2)[2]), json.loads(after_fake[key].split("|", 2)[2])
    assert p_fake["components"]["fundamental"] != p_real["components"]["fundamental"]  # the canary filing is read once public


# ------------------------------------------------------------------------------------------ 4. network blocked
def test_the_backtest_completes_with_every_socket_refused(world, tmp_path):
    _p, eng, data = world
    store = BacktestStore(data)
    reg, replay, blocked = build_offline_registry(eng, store)
    e = Engine(store, reg, backtest_config(), str(tmp_path / "rows.db"), replay=replay)
    with no_network() as refused:
        with pytest.raises(SocketBlocked):
            socket.create_connection(("example.com", 443))
        info = e.run(weekly_times(date(2026, 5, 1), date(2026, 5, 29)), log=lambda _m: None)
    assert [w["eligible"] for w in info] == [3] * len(info) and all(w["macro"] for w in info)
    assert sum(b.attempts for b in blocked.values()) > 0  # the providers were asked and refused
    assert replay.served > 0 and replay.misses == []
    assert len(refused) == 1  # only the deliberate connection above


# ------------------------------------------------------------------------------------------ 5. shuffled returns
def test_shuffled_returns_give_an_ic_near_zero_while_the_real_relation_is_found():
    rng = random.Random(7)
    real, shuffled = [], []
    for _ in range(200):
        f = [rng.gauss(0, 1) for _ in range(150)]
        y = [0.3 * a + rng.gauss(0, 1) for a in f]
        real.append(spearman(f, y))
        rng.shuffle(y)
        shuffled.append(spearman(f, y))
    assert sum(real) / len(real) > 0.2
    assert abs(sum(shuffled) / len(shuffled)) < 0.02


# ------------------------------------------------------------------------------------------ engine pieces
def test_backtest_config_changes_only_the_documented_settings():
    from marketlens.application.pipeline import COVERAGE_VETO_COMPONENTS
    from marketlens.config import load_model_config

    op, bt = load_model_config(), backtest_config()
    assert bt.scoring_model.weights["earnings_revision"] == 0 and bt.scoring_model.weights["catalyst"] == 0
    for k, w in op.scoring_model.weights.items():
        if k not in ("earnings_revision", "catalyst"):
            assert bt.scoring_model.weights[k] == w
    assert bt.decision.min_completeness == BACKTEST_MIN_COMPLETENESS == 0.3
    assert replace(bt.decision, min_completeness=op.decision.min_completeness) == op.decision
    assert bt.raw["backtest"] == {"coverage_veto_components": ["fundamental"]}
    assert "backtest" not in op.raw and COVERAGE_VETO_COMPONENTS == ("fundamental", "valuation")  # the operating veto is untouched
    assert bt.entry == op.entry and bt.scanner == op.scanner and bt.paper == op.paper and bt.sector_models == op.sector_models


def test_bars_are_split_adjusted_only_with_splits_executed_by_t(world):
    _p, _eng, data = world
    store = BacktestStore(data)
    store.set_time(datetime(2025, 11, 7, 20, tzinfo=NY))  # before the 4-for-1 split
    before = store.bars("AAA", date(2025, 11, 3), date(2025, 11, 7))
    store.set_time(datetime(2025, 11, 14, 20, tzinfo=NY))  # after it
    after = store.bars("AAA", date(2025, 11, 3), date(2025, 11, 14))
    raw = {d: r[3] for d, r in zip(data.by_key[next(k for k in data.by_key if k.startswith("AAA"))].days, data.by_key[next(k for k in data.by_key if k.startswith("AAA"))].rows)}
    assert before[-1].close == raw[date(2025, 11, 7)]  # as traded: no future split applied
    assert after[4].day == date(2025, 11, 7) and after[4].close == pytest.approx(raw[date(2025, 11, 7)] / 4)
    assert store.splits("AAA")[0].execution_date == FX.SPLIT_DAY


def test_a_rename_is_one_security_and_the_universe_uses_the_ticker_of_t(world):
    _p, _eng, data = world
    ln = next(x for x in data.lineages if "NEWC" in x.labels)
    assert "OLDC" in ln.labels and ln.days[0] == FX.START  # one history across the rename
    store = BacktestStore(data)
    store.set_time(datetime(2025, 9, 26, 20, tzinfo=NY))
    assert "OLDC" in store.labels() and "NEWC" not in store.labels()
    store.set_time(datetime(2025, 10, 3, 20, tzinfo=NY))
    assert "NEWC" in store.labels() and "OLDC" not in store.labels()


def test_market_cap_uses_shares_known_at_t_and_fundamentals_hide_later_restatements(world):
    _p, _eng, data = world
    store = BacktestStore(data)
    store.set_time(datetime(2025, 12, 12, 20, tzinfo=NY))  # before the restatement filed 2025-12-15
    q = {x.period_end: x for x in store.quarters("AAA", None)}
    assert q[FX.RESTATED[0]].revisions == {}
    store.set_time(datetime(2025, 12, 19, 20, tzinfo=NY))
    q = {x.period_end: x for x in store.quarters("AAA", None)}
    assert q[FX.RESTATED[0]].revisions["revenue"][0][0] == FX.RESTATED[1]
    sec = next(s for s in store.securities() if s.ticker == "AAA")
    close = store.bars("AAA", store.session, store.session)[-1].close
    assert sec.market_cap == pytest.approx(close * 6.1e9 * 4)  # shares filed before the split, on the post-split basis


def test_total_return_applies_the_split_and_reinvests_the_dividend(world):
    _p, _eng, data = world
    aaa = next(x for x in data.lineages if x.labels[0] == "AAA")
    path = total_return_path(aaa, date(2025, 11, 10), date(2025, 11, 14))
    rows = {d: r for d, r in zip(aaa.days, aaa.rows)}
    expect = rows[date(2025, 11, 14)][3] * 4 / rows[date(2025, 11, 10)][0]
    assert path[-1][2] == pytest.approx(expect)  # no fake −75 % on the split day
    bbb = next(x for x in data.lineages if x.labels[0] == "BBB")
    p2 = total_return_path(bbb, date(2025, 11, 19), date(2025, 11, 20))
    r = {d: v for d, v in zip(bbb.days, bbb.rows)}
    assert p2[-1][2] == pytest.approx((r[date(2025, 11, 19)][3] / r[date(2025, 11, 19)][0]) * (r[date(2025, 11, 20)][3] + 1.4) / r[date(2025, 11, 19)][3])


def test_a_delisted_security_is_liquidated_at_its_last_close_and_recomputed_with_losses(world):
    _p, _eng, data = world
    gone = next(x for x in data.lineages if x.labels[0] == "GONE")
    o = outcome(gone, date(2025, 11, 28), 20, data.last_session)
    assert o is not None and o.delisted and o.last_day == FX.GONE_LAST
    assert o.ret[0.30] == pytest.approx((1 + o.ret[0.0]) * 0.7 - 1) and o.ret[0.55] == pytest.approx((1 + o.ret[0.0]) * 0.45 - 1)
    aaa = next(x for x in data.lineages if x.labels[0] == "AAA")
    assert outcome(aaa, date(2026, 6, 26), 20, data.last_session) is None  # not matured within the data


def test_the_strategy_sells_a_delisted_position_at_the_assumed_loss(world):
    from marketlens.backtest.strategy import run_strategy
    from marketlens.config import load_model_config

    _p, _eng, data = world
    store = BacktestStore(data)
    key = next(k for k in data.by_key if k.startswith("GONE"))
    t = datetime(2025, 11, 28, 20, tzinfo=NY)
    last = data.by_key[key].rows[-1][3]
    sig = Signal(t, key, "GONE", "BUY", 80.0, 70.0, stop=last * 0.5, target1=last * 3, target2=last * 4, max_buy=last * 2, adv20=5e7, regime="x", sector="Technology")
    bars = {key: final_basis_bars(store, key, date(2100, 1, 1))}
    res = {}
    for loss in (0.0, 0.55):
        res[loss] = run_strategy([sig], {}, lambda k: data.by_key[k].splits, bars, load_model_config().paper, data.last_session, 0.0, loss, [], {})
    assert res[0.0]["trades"] == 1 and res[0.55]["final_equity"] < res[0.0]["final_equity"]
    pos = 10_000.0  # the app's base notional for a BUY
    assert res[0.0]["final_equity"] - res[0.55]["final_equity"] == pytest.approx(pos / sig_entry(data, key, t) * last * 0.55, rel=1e-3)


def sig_entry(data, key, t):
    ln = data.by_key[key]
    i = next(i for i, d in enumerate(ln.days) if d > t.date())
    return ln.rows[i][0]


def test_costs_follow_the_preregistered_half_spread_table():
    assert half_spread(150e6) == 0.0002 and half_spread(50e6) == 0.0005 and half_spread(5e6) == 0.0015 and half_spread(None) == 0.0015


# ------------------------------------------------------------------------------------------ statistics
def test_rank_ic_uses_average_ranks_for_ties_and_skips_constant_factors():
    assert ranks([3, 1, 3, 2]) == [3.5, 1, 3.5, 2]
    assert spearman([1, 1, 1, 1], [1, 2, 3, 4]) is None
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert quintiles(list(range(10))) == [1, 1, 2, 2, 3, 3, 4, 4, 5, 5]


def test_newey_west_lag_and_holm():
    assert nw_lag(20) == 3 and nw_lag(60) == 11
    t, p = newey_west_t([0.05, 0.04, 0.06, 0.05, 0.03, 0.07, 0.05, 0.04], 3)
    assert t > 5 and p < 1e-3
    adj = holm({"a": 0.01, "b": 0.04, "c": 0.03, "d": None})
    assert adj == {"a": 0.03, "c": 0.06, "b": 0.06, "d": None}


# ------------------------------------------------------------------------------------------ reproduction
def test_the_reproduction_command_gives_the_same_results_hash(world, tmp_path):
    from marketlens.backtest.run import main

    path, _eng, _data = world
    args = ["--db", path, "--start", "2026-03-02", "--end", "2026-06-26", "--min-names", "2"]
    a = main(args + ["--out", str(tmp_path / "a"), "--leak-checks", "2"])
    b = main(args + ["--out", str(tmp_path / "b"), "--leak-checks", "0"])
    assert a["results_sha256"] == b["results_sha256"] and a["sockets_refused"] == 0 and a["fred_replay_misses"] == 0
    leak = json.loads((tmp_path / "a" / "leak_checks.json").read_text())
    assert leak["truncated_all_equal"] and leak["canary"]["unchanged_at_t"] and leak["canary"]["visible_after"]
