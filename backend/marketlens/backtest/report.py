"""From the engine's rows to ``results.json`` (PREREGISTRATION §측정). Deterministic: same rows + data → same bytes.

Everything here is evaluation: forward returns are computed after the analyses and never flow back into them.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.engine import Engine

from marketlens.backtest.engine import UNVERIFIABLE_COMPONENTS, bt_rows, one_way_cost
from marketlens.backtest.metrics import (block_bootstrap_ci, demean_by, holm, ic_summary, quintiles, residualize, spearman, winsorize)
from marketlens.backtest.outcomes import DELISTING, HORIZONS, daily_outliers, fx_return, outcome, spy_return
from marketlens.backtest.schema import bt_series
from marketlens.backtest.store import BacktestData
from marketlens.domain.scoring import COMPONENTS

COSTS = (0.0, 1.0, 2.0)
MAIN_H = 60
RISK_ELEMENTS = ("risk",)


def load_series(eng: Engine) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = {}
    with eng.connect() as c:
        for r in c.execute(select(bt_series).order_by(bt_series.c.series_id, bt_series.c.day)):
            out.setdefault(r.series_id, []).append((r.day, float(r.value)))
    return out


def element_values(p: dict[str, Any]) -> dict[str, float]:
    v = {f"component.{n}": p["components"][n]["sub"] for n in COMPONENTS}
    v["score"] = p["total"]
    v["sell_score"] = p["sell_total"]
    return v


def _r(x: Any, nd: int = 8) -> Any:
    if isinstance(x, float):
        return None if math.isnan(x) or math.isinf(x) else round(x, nd)
    if isinstance(x, dict):
        return {k: _r(v, nd) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_r(v, nd) for v in x]
    return x


def regimes_at(t_day: date, spy_closes: list[tuple[date, float]], series: dict[str, list[tuple[date, float]]]) -> dict[str, str | None]:
    from bisect import bisect_right

    def upto(s: list[tuple[date, float]]) -> list[float]:
        i = bisect_right(s, (t_day, float("inf")))
        return [v for _, v in s[:i]]

    spy = upto(spy_closes)
    vix = upto(series.get("VIXCLS", []))
    y10 = upto(series.get("DGS10", []))
    out: dict[str, str | None] = {}
    out["spy_200d"] = (("above" if spy[-1] > sum(spy[-200:]) / 200 else "below") if len(spy) >= 200 else None)
    if len(vix) >= 252:
        w = sorted(vix[-252:])
        med = (w[125] + w[126]) / 2
        out["vix_252d_median"] = "above" if vix[-1] > med else "below"
    else:
        out["vix_252d_median"] = None
    out["dgs10_63d_change"] = (("up" if y10[-1] - y10[-64] > 0 else "down") if len(y10) >= 64 else None)
    return out


def build(results: Engine, data: BacktestData, bt: Engine, signals_state: Any, paper_base: Any, min_names: int = 100,
          seed: int = 20260928) -> dict[str, Any]:
    from marketlens.backtest.strategy import run_strategy

    series = load_series(bt)
    data_end = data.last_session
    spy_ln = next((ln for ln in data.lineages if ln.labels and ln.labels[-1] == "SPY" and ln.cik is None), None)
    with results.connect() as c:
        # plain namespaces: a SQLAlchemy Row's ``.t`` is its typed-tuple accessor, not the column
        from types import SimpleNamespace

        rows = [SimpleNamespace(**dict(r._mapping)) for r in c.execute(select(bt_rows).order_by(bt_rows.c.t, bt_rows.c.key))]
    by_t: dict[str, list[Any]] = {}
    universe: dict[str, dict[str, int]] = {}
    ended = {ln.key for ln in data.lineages if data_end is not None and (data_end - ln.days[-1]).days > 7}
    for r in rows:
        by_t.setdefault(r.t, []).append(r)
        u = universe.setdefault(r.t, {"universe": 0, "eligible": 0, "eligible_later_delisted": 0})
        u["universe"] += 1
        u["eligible"] += r.eligible
        u["eligible_later_delisted"] += int(bool(r.eligible) and r.key in ended)  # survivorship check: must not be 0 throughout
    spy_closes = [(d, row[3]) for d, row in zip(spy_ln.days, spy_ln.rows)] if spy_ln else []

    # ---------------------------------------------------------------- cross-sections with outcomes
    xs: dict[int, dict[str, list[dict[str, Any]]]] = {h: {} for h in HORIZONS}
    spy_by: dict[int, dict[str, float]] = {h: {} for h in HORIZONS}
    regimes: dict[str, dict[str, str | None]] = {}
    for t, rs in sorted(by_t.items()):
        t_day = datetime.fromisoformat(t).date()
        regimes[t] = regimes_at(t_day, spy_closes, series)
        for h in HORIZONS:
            sr = spy_return(spy_ln, t_day, h) if spy_ln else None
            if sr is None:
                continue
            spy_by[h][t] = sr
            cs = []
            for r in rs:
                if not r.eligible:
                    continue
                ln = data.by_key.get(r.key)
                o = outcome(ln, t_day, h, data_end) if ln else None
                if o is None:
                    continue
                p = json.loads(r.payload)
                fx = fx_return(series.get("DEXKOUS", []), o.entry, o.end)
                cs.append({"key": r.key, "sector": r.sector or "Unknown", "p": p, "ret": o.ret, "delisted": o.delisted, "fx": fx,
                           "beta": p.get("beta252"), "mcap": p.get("market_cap"), "adv": p.get("adv20")})
            xs[h][t] = cs

    elements = sorted(element_values(json.loads(next(r.payload for r in rows if r.eligible))).keys()) if any(r.eligible for r in rows) else []
    unverifiable = {f"component.{n}" for n in UNVERIFIABLE_COMPONENTS}
    out: dict[str, Any] = {"horizons": {}, "universe_per_week": universe, "regimes_per_week": regimes}

    for h in HORIZONS:
        hres: dict[str, Any] = {"elements": {}, "quintiles": {}, "weeks_used": 0}
        weeks = [t for t in sorted(xs[h]) if len(xs[h][t]) >= min_names]
        hres["weeks_used"] = len(weeks)
        hres["weeks_excluded_small"] = sorted(t for t in xs[h] if len(xs[h][t]) < min_names)
        pvals: dict[str, float | None] = {}
        for el in elements:
            if el in unverifiable:
                hres["elements"][el] = {"verdict": "검증 불가", "reason": "과거 시점 값이 없음 — 백테스트 가중치 0(상수)"}
                continue
            ic, ic_sn, ic_ra, cov, by_regime, by_sector = [], [], [], [], {}, {}
            for t in weeks:
                cs = xs[h][t]
                f = [element_values(x["p"])[el] for x in cs]
                y = [x["ret"][0.0] for x in cs]
                avail = [x["p"]["components"][el.split(".", 1)[1]]["available"] for x in cs] if el.startswith("component.") else [True] * len(cs)
                cov.append(sum(avail) / len(avail))
                v = spearman(f, y)
                if v is None:
                    continue
                ic.append(v)
                secs = [x["sector"] for x in cs]
                sn = spearman(demean_by(f, secs), demean_by(y, secs))
                if sn is not None:
                    ic_sn.append(sn)
                ok = [i for i, x in enumerate(cs) if x["beta"] is not None and x["mcap"]]
                res = residualize([y[i] for i in ok], [[cs[i]["beta"] for i in ok], [math.log(cs[i]["mcap"]) for i in ok]])
                if res is not None:
                    ra = spearman([f[i] for i in ok], res)
                    if ra is not None:
                        ic_ra.append(ra)
                for rk, rv in regimes[t].items():
                    if rv is not None:
                        by_regime.setdefault(f"{rk}={rv}", []).append(v)
                sec_groups: dict[str, list[int]] = {}
                for i, s in enumerate(secs):
                    sec_groups.setdefault(s, []).append(i)
                for s, idx in sec_groups.items():
                    if len(idx) >= 20:
                        sv = spearman([f[i] for i in idx], [y[i] for i in idx])
                        if sv is not None:
                            by_sector.setdefault(s, []).append(sv)
            summ = ic_summary(ic, h)
            pvals[el] = summ["p"]  # type: ignore[assignment]
            hres["elements"][el] = {
                **summ, "coverage_mean": (sum(cov) / len(cov)) if cov else None,
                "sector_neutral": {"mean_ic": (sum(ic_sn) / len(ic_sn)) if ic_sn else None, "weeks": len(ic_sn)},
                "risk_adjusted": {"mean_ic": (sum(ic_ra) / len(ic_ra)) if ic_ra else None, "weeks": len(ic_ra)},
                "by_regime": {k: {"mean_ic": sum(v) / len(v), "weeks": len(v)} for k, v in sorted(by_regime.items())},
                "by_sector": {k: {"mean_ic": sum(v) / len(v), "weeks": len(v)} for k, v in sorted(by_sector.items()) if len(v) >= 4},
                "weekly_ic": ic,
            }
        adj = holm(pvals)
        for el, p in adj.items():
            hres["elements"][el]["holm_p"] = p
            e = hres["elements"][el]
            if e.get("mean_ic") is None:
                e["verdict"] = "측정 불가(주 수 부족)"
                continue
            a = e["mean_ic"] > 0 and p is not None and p < 0.05
            b = e["mean_ic"] >= 0.02
            c_ = e["blocks"] > 0 and e["blocks_positive"] * 3 >= 2 * e["blocks"]
            sn, ra = e["sector_neutral"]["mean_ic"], e["risk_adjusted"]["mean_ic"]
            d = sn is not None and ra is not None and (sn > 0) == (e["mean_ic"] > 0) and (ra > 0) == (e["mean_ic"] > 0)
            e["criteria"] = {"a": a, "b": b, "c": c_, "d": d}
            n = sum((a, b, c_, d))
            e["verdict"] = "예비: " + ("유효" if n == 4 else "효과 없음" if n == 0 else "약함")

        # ------------------------------------------------------------ quintiles of the verifiable total
        for k in COSTS:
            for loss in DELISTING:
                for wins in (False, True):
                    q_ret: dict[int, list[float]] = {q: [] for q in range(1, 6)}
                    q_ex_spy: dict[int, list[float]] = {q: [] for q in range(1, 6)}
                    q_ex_ew: dict[int, list[float]] = {q: [] for q in range(1, 6)}
                    q_hit: dict[int, list[float]] = {q: [] for q in range(1, 6)}
                    q_worst: dict[int, float] = {}
                    q_krw: dict[int, list[float]] = {q: [] for q in range(1, 6)}
                    tmb = []
                    for t in weeks:
                        cs = xs[h][t]
                        net = [x["ret"][loss] - 2 * one_way_cost(x["adv"], k) for x in cs]
                        if wins:
                            net = winsorize(net)
                        qs = quintiles([x["p"]["total"] for x in cs])
                        ew = sum(net) / len(net)
                        spy_r = spy_by[h][t]
                        means = {}
                        for q in range(1, 6):
                            idx = [i for i, qq in enumerate(qs) if qq == q]
                            if not idx:
                                continue
                            m = sum(net[i] for i in idx) / len(idx)
                            means[q] = m
                            q_ret[q].append(m)
                            q_ex_spy[q].append(m - spy_r)
                            q_ex_ew[q].append(m - ew)
                            q_hit[q].append(sum(1 for i in idx if net[i] > spy_r) / len(idx))
                            q_worst[q] = min(q_worst.get(q, 0.0), min(net[i] for i in idx))
                            fxs = [(1 + net[i]) * (1 + cs[i]["fx"]) - 1 for i in idx if cs[i]["fx"] is not None]
                            if fxs:
                                q_krw[q].append(sum(fxs) / len(fxs))
                        if 1 in means and 5 in means:
                            tmb.append(means[5] - means[1])
                    lo, hi = block_bootstrap_ci(tmb, seed=seed)
                    mean = lambda v: (sum(v) / len(v)) if v else None  # noqa: E731
                    hres["quintiles"][f"cost{k:g}x_delist{int(loss * 100)}{'_wins' if wins else ''}"] = {
                        str(q): {"mean_ret": mean(q_ret[q]), "excess_spy": mean(q_ex_spy[q]), "excess_ew": mean(q_ex_ew[q]),
                                 "hit_rate_vs_spy": mean(q_hit[q]), "worst_h_return": q_worst.get(q), "mean_ret_krw": mean(q_krw[q])} for q in range(1, 6)
                    } | {"top_minus_bottom": {"mean": mean(tmb), "ci90_block12": [lo, hi], "weeks": len(tmb)}}
        out["horizons"][str(h)] = hres

    # ---------------------------------------------------------------- risk elements: drawdown / volatility by risk quintile
    risk_view: dict[str, Any] = {}
    h = 20
    for q in range(1, 6):
        risk_view[str(q)] = {"mean_ret": None, "sd_ret": None, "worst": None}
    buckets: dict[int, list[float]] = {q: [] for q in range(1, 6)}
    for t in [t for t in sorted(xs[h]) if len(xs[h][t]) >= min_names]:
        cs = xs[h][t]
        qs = quintiles([x["p"]["components"]["risk"]["sub"] for x in cs])
        for x, qq in zip(cs, qs):
            buckets[qq].append(x["ret"][0.0])
    for q, v in buckets.items():
        if v:
            mu = sum(v) / len(v)
            risk_view[str(q)] = {"mean_ret": mu, "sd_ret": math.sqrt(sum((a - mu) ** 2 for a in v) / max(1, len(v) - 1)), "worst": min(v), "n": len(v)}
    out["risk_component_by_quintile_h20"] = risk_view

    # ---------------------------------------------------------------- shuffled-return check (computation, not leaks)
    rng = random.Random(seed)
    sh = []
    for t in [t for t in sorted(xs[MAIN_H]) if len(xs[MAIN_H][t]) >= min_names]:
        cs = xs[MAIN_H][t]
        y = [x["ret"][0.0] for x in cs]
        rng.shuffle(y)
        v = spearman([x["p"]["total"] for x in cs], y)
        if v is not None:
            sh.append(v)
    out["shuffle_check"] = {"weeks": len(sh), "mean_ic": (sum(sh) / len(sh)) if sh else None, "nw": ic_summary(sh, MAIN_H) if sh else None}

    # ---------------------------------------------------------------- the app-rule strategy
    spy_tr = {}
    if spy_ln:
        from marketlens.backtest.store import total_return_path

        spy_tr = {d: c for d, _o, c in total_return_path(spy_ln, spy_ln.days[0], spy_ln.days[-1])}
    strat: dict[str, Any] = {}
    if signals_state is not None:
        from marketlens.backtest.engine import FAR_FUTURE, final_basis_bars

        sigs, later, split_of, store = signals_state
        keys = sorted({s.key for s in sigs})
        bars = {k: final_basis_bars(store, k, FAR_FUTURE) for k in keys}
        for k in COSTS:
            for loss in DELISTING:
                strat[f"cost{k:g}x_delist{int(loss * 100)}"] = run_strategy(sigs, later, split_of, bars, paper_base, data_end, k, loss,
                                                                             series.get("DTB3", []), spy_tr)
        strat["signals"] = len(sigs)
    out["strategy"] = strat
    out["outliers_daily_50pct"] = daily_outliers(data)[:200]
    out["outliers_daily_50pct_count"] = len(daily_outliers(data))
    return _r(out)


def dump(results: dict[str, Any], path: str) -> str:
    import hashlib

    blob = json.dumps(results, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    with open(path, "wb") as f:
        f.write(blob)
    return hashlib.sha256(blob).hexdigest()

