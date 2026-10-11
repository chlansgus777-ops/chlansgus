"""Yardsticks for the monthly routine (M-NF, 대형주 모멘텀): the same account, engine and costs as §20, compared with

* SPY bought and held (dividends reinvested) — already in §20, recomputed here on the same days;
* a dollar deposit: the 3-month US Treasury bill rate (FRED DTB3), interest accrued every calendar day;
* random buying: the same universe, slots, sector limit, month-end schedule and keep rule as M-NF, but each name's
  rank is a coin (a fixed random number per name and seed) instead of its 12-1 momentum. Many seeds; the spread shows
  what luck alone gave with these rules.

    python -m marketlens.backtest.benchmarks --db backtest.db --out benchmarks.json [--seeds 50]

Nothing here changes the routine or its verdict (PREREGISTRATION §20 stays as registered); it only adds yardsticks.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import urllib.request
from datetime import date, datetime
from typing import Any, Sequence

import numpy as np

from marketlens.backtest import portfolio as P
from marketlens.domain import strategies as S


def monthly(eq: Sequence[tuple[date, float]]) -> list[tuple[str, float]]:
    """The last value of each month, scaled to start at 100."""
    out: dict[str, float] = {}
    for d, v in eq:
        out[f"{d.year:04d}-{d.month:02d}"] = v
    base = eq[0][1]
    return [(k, round(v / base * 100, 2)) for k, v in out.items()]


def worst_12m(eq: Sequence[tuple[date, float]]) -> dict[str, Any] | None:
    """The worst return over any 12 months (252 sessions) and when it ended."""
    if len(eq) <= 252:
        return None
    v = np.array([x for _, x in eq], dtype=float)
    r = v[252:] / v[:-252] - 1
    k = int(np.argmin(r))
    return {"return": float(r[k]), "start": eq[k][0].isoformat(), "end": eq[k + 252][0].isoformat()}


def drawdown_span(eq: Sequence[tuple[date, float]]) -> dict[str, Any] | None:
    """The deepest fall from a peak: when the peak was, the bottom, and when (if) the old peak was regained."""
    if len(eq) < 2:
        return None
    v = np.array([x for _, x in eq], dtype=float)
    peak = np.maximum.accumulate(v)
    dd = v / peak - 1
    b = int(np.argmin(dd))
    p = int(np.argmax(v[: b + 1]))
    back = next((i for i in range(b, len(v)) if v[i] >= v[p]), None)
    return {"max_drawdown": float(dd[b]), "peak": eq[p][0].isoformat(), "bottom": eq[b][0].isoformat(),
            "recovered": eq[back][0].isoformat() if back is not None else None}


def fred_dtb3(start: date, end: date) -> list[tuple[date, float]]:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise SystemExit("FRED_API_KEY is needed for the deposit yardstick (DTB3)")
    url = ("https://api.stlouisfed.org/fred/series/observations?series_id=DTB3&file_type=json"
           f"&observation_start={start.isoformat()}&observation_end={end.isoformat()}&api_key={key}")
    with urllib.request.urlopen(url, timeout=60) as r:
        obs = json.load(r)["observations"]
    return [(date.fromisoformat(o["date"]), float(o["value"]) / 100) for o in obs if o["value"] not in (".", "")]


def deposit_curve(cal: Sequence[date], rates: Sequence[tuple[date, float]]) -> list[tuple[date, float]]:
    """Each session's value: the latest published rate before it, accrued for the calendar days since the last session."""
    out = [(cal[0], P.START_CASH)]
    v = P.START_CASH
    j = 0
    rate = rates[0][1]
    for a, b in zip(cal, cal[1:]):
        while j < len(rates) and rates[j][0] <= a:
            rate = rates[j][1]
            j += 1
        v *= 1 + rate * (b - a).days / 365
        out.append((b, v))
    return out


def eligible_by_month(secs: dict[str, P.Sec], cal: Sequence[date]) -> dict[date, list[str]]:
    """The names M-NF could pick at each month-end: tradable, large by 20-day dollar volume, a 12-1 momentum on record."""
    thr = P.large_cap_thresholds(secs, cal)
    mom = {k: S.momentum(s.c) for k, s in secs.items()}
    trad = {k: S.tradable(s.ind) for k, s in secs.items()}
    out: dict[date, list[str]] = {}
    for d in sorted(S.month_ends(list(cal))):
        names = []
        for k, s in secs.items():
            i = s.index.get(d)
            if i is None or not trad[k][i] or not float(s.ind["dv20"][i]) >= thr[d] or not mom[k][i] == mom[k][i]:
                continue
            names.append(k)
        out[d] = names
    return out


def random_candidates(elig: dict[date, list[str]], seed: int) -> tuple[dict[str, dict[date, list[tuple[float, str]]]], Any]:
    """M-NF's universe and schedule with a coin for the rank: each name gets one fixed random number per seed; at a
    month-end the top 20 of the eligible names are bought and a holding stays while it is in the top 40."""
    rng = random.Random(seed)
    coin: dict[str, float] = {}
    rn: dict[date, list[tuple[float, str]]] = {}
    keep: dict[date, set[str]] = {}
    for d, names in elig.items():
        for k in sorted(names):
            if k not in coin:
                coin[k] = rng.random()
        ranked = sorted(names, key=lambda k: (-coin[k], k))
        rn[d] = [(coin[k], k) for k in ranked[: S.MOM_TOP]]
        keep[d] = set(ranked[: S.MOM_KEEP])

    def xexit(strategy: str, key: str, d: date) -> str | None:
        if d not in keep:
            return None
        return None if key in keep[d] else "rule"

    return {"RND": rn}, xexit


def summary(eq: Sequence[tuple[date, float]]) -> dict[str, Any]:
    st = P.series_stats(eq)
    return {k: st.get(k) for k in ("cagr", "total_return", "volatility", "sharpe", "max_drawdown")} | {
        "by_year": P.by_year(eq), "worst_12m": worst_12m(eq), "drawdown": drawdown_span(eq), "monthly": monthly(eq)}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds", type=int, default=50)
    a = ap.parse_args(argv)
    secs, spy, cal = P.load(a.db)
    full_cal = [d for d in cal if P.FULL[0] <= d <= P.FULL[1]]
    print(f"securities {len(secs)} · sessions {len(cal)} · measured {full_cal[0]} ~ {full_cal[-1]}", flush=True)
    up = P.spy_up_map(spy)

    # the routine itself, recomputed so its monthly curve sits next to the yardsticks (must match §20's numbers)
    cands, xexit = P.s20_candidates(secs, spy, cal, up)
    mnf = next(v for v in P.S20_VARIANTS if v.id == "M-NF")
    sim = P.simulate(mnf, secs, spy, cal, cands, P.COST, *P.FULL, xexit=xexit)
    routine = summary(sim["equity"]) | {"trades": len(sim["trades"])}
    print(f"M-NF: cagr {routine['cagr']:.4f} mdd {routine['max_drawdown']:.4f} trades {routine['trades']}", flush=True)

    spy_eq = P.spy_total_return(spy, *P.FULL)
    rates = fred_dtb3(date(P.FULL[0].year - 1, 12, 1), P.FULL[1])
    dep_eq = deposit_curve(full_cal, rates)

    rnd = P.Variant("RND", "무작위 매수", (P.Sleeve("RND", S.MOM_TOP),), sector_max=mnf.sector_max)
    elig = eligible_by_month(secs, cal)
    runs = []
    for seed in range(a.seeds):
        rc, rx = random_candidates(elig, seed)
        r = P.simulate(rnd, secs, spy, cal, rc, P.COST, *P.FULL, xexit=rx)
        st = P.series_stats(r["equity"])
        runs.append({"seed": seed, "cagr": st["cagr"], "max_drawdown": st["max_drawdown"], "sharpe": st["sharpe"],
                     "volatility": st["volatility"], "trades": len(r["trades"]), "equity": r["equity"]})
        print(f"random {seed}: cagr {st['cagr']:.4f} mdd {st['max_drawdown']:.4f}", flush=True)
    cagrs = np.array([x["cagr"] for x in runs])
    med = runs[int(np.argsort(cagrs)[len(cagrs) // 2])]
    pct = lambda q: float(np.percentile(cagrs, q))  # noqa: E731
    random_block = {
        "seeds": a.seeds, "median_seed": med["seed"],
        "cagr": {"p10": pct(10), "median": pct(50), "p90": pct(90), "min": float(cagrs.min()), "max": float(cagrs.max())},
        "max_drawdown_median": float(np.median([x["max_drawdown"] for x in runs])),
        "volatility_median": float(np.median([x["volatility"] for x in runs])),
        "sharpe_median": float(np.median([x["sharpe"] for x in runs if x["sharpe"] is not None])),
        "routine_beats_share": float(np.mean(cagrs < routine["cagr"])),
        "median_run": summary(med["equity"]),
        "runs": [{k: v for k, v in x.items() if k != "equity"} for x in runs],
    }
    res = {
        "note": "루틴(M-NF) 비교 기준 — 같은 자료·엔진·비용(한쪽 0.15%)·계좌 규칙. 과거 시뮬레이션이며 미래 수익을 보장하지 않습니다.",
        "period": {"start": full_cal[0].isoformat(), "end": full_cal[-1].isoformat()},
        "routine": routine,
        "spy": summary(spy_eq),
        "deposit": summary(dep_eq) | {"series": "FRED DTB3 (미국 3개월 국채 금리)", "observations": len(rates)},
        "random": random_block,
        "source": {"commit": os.environ.get("GITHUB_SHA"), "workflow_run": os.environ.get("GITHUB_RUN_ID"), "data_sha256": P._sha(a.db),
                   "finished_at": datetime.utcnow().isoformat() + "Z", "cost": P.COST, "start_cash": P.START_CASH},
    }
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=1, default=str)


if __name__ == "__main__":
    main()
