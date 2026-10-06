"""대형주 모멘텀 (M-NF-1.0) in the app — the owner's choice of 2026-10-06 ("리스크없는 돈벌이는 없어 대형주 모멘텀으로
간다") after PREREGISTRATION §20, where it beat SPY on return but failed two of the six checks (Sharpe, concentration).
It is shown with that verdict and its risks, never as a verified strategy, and the app places no orders.

The SAME rules as the backtest (domain/strategies.py: momentum, cap_threshold, momentum_ranks; the monthly book of
backtest/portfolio.py): at each month-end close, rank the day's large caps (top 500 by 20-session dollar volume inside
the common universe) by 12-1 month return; hold the top 20 (5 % each, at most 5 per sector); keep a holding while it
stays inside the top 40 and the large caps; trade at the next session's open.

What the book holds is rebuilt from a FIXED start (BOOK_FROM), so it changes only at a month-end, never because a
moving window dropped an earlier month (independent review 0444a21 F03). Every name with stored bars is ranked — not
only today's securities list — so a name that later delisted keeps its place in the month it was ranked (review 2 F03).

The forward record (data_dir/momentum_forward[_<mode>].jsonl, append-only, one file per data mode) keeps the latest
month-end's ranking when it is first seen, with the time it was seen. That snapshot is the month's ranking from then on
(never recomputed), as long as today's data still gives the same 12-1 returns for its names; a record from other data
(MOCK vs LIVE, a changed source) is set aside (review F02). Only a snapshot seen before the next open counts in the paper
account; one first seen later is kept as late and never filled (review F04, review 2 F01). The paper account is the
backtest's own engine on the stored bars from BOOK_FROM on (review 2 F02); stored bars carry no dividends, so it is a
price-only record.
"""

from __future__ import annotations

import json
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

from marketlens.domain import strategies as S
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import is_trading_day, last_completed_session, next_trading_day, session_open_utc

CHOSEN_ON = date(2026, 10, 6)  # the owner's choice: the forward record starts at the first month-end after it
HISTORY_DAYS = 420  # calendar days of bars a ranking needs: 252 sessions of momentum + the 20-session dollar volume
BOOK_FROM = CHOSEN_ON - timedelta(days=HISTORY_DAYS)  # fixed: the book is rebuilt from here on every time
# a month-end is ranked only when the names with a year of bars there are at least MIN_COVERAGE of the liquid names
# trading that day (and at least min_names): a store still filling its history must not rank the few names it has
# (owner report 2026-10-06: the 08-31 ranking with one name)
MIN_COVERAGE = 0.9
MIN_NAMES = S.MOM_KEEP
SPEC = S.StrategySpec(
    "MN", "대형주 모멘텀", "M-NF-1.0",
    entry=("매달 마지막 거래일 종가에 대형주(그날 20일 평균 거래대금 상위 500) 안에서 12개월 전→1개월 전 수익률 상위 20위",
           "다음 거래일 시가에 빈 칸을 채움 · 20칸 × 5% · 업종당 최대 5종목"),
    exit=("월말 순위가 40위 밖이거나 대형주에서 빠지면 다음 거래일 시가에 매도", "시장 필터·손절·목표가 없음"),
    max_hold=None, needs_spy=False)
RISK_KO = ("백테스트(2017~2026)에서 최대 낙폭 −61%, 변동성 SPY의 2.4배였습니다. 몇 달 연속 크게 잃을 수 있습니다.",
           "이익의 대부분이 소수 종목(CVNA·APP·RKLB·ASTS·SMCI)에서 나왔습니다. 그런 종목이 다시 나온다는 보장은 없습니다.",
           "2017~2023년만 보면 연 8.6%로 SPY(13.1%)보다 낮았습니다. 최근 3년의 급등주 장세에 크게 기댄 결과입니다.")
Prep = Mapping[str, tuple[dict[date, int], np.ndarray, np.ndarray, np.ndarray, np.ndarray]]


def month_end(d: date) -> bool:
    return is_trading_day(d) and next_trading_day(d).month != d.month


def prepare(bars: Mapping[str, list[Bar]], names, through: date) -> dict[str, tuple[dict[date, int], np.ndarray, np.ndarray, np.ndarray, np.ndarray]]:
    """Per name, once: (day → index, tradable, 20-session dollar volume, 12-1 momentum, liquid) on bars up to ``through``.
    Liquid = the common universe's price and dollar-volume floors without the year of history."""
    out = {}
    for t in names:
        bs = [b for b in bars.get(t, ()) if b.day <= through]
        if not bs:
            continue
        c = np.array([b.close for b in bs], dtype=float)
        v = np.array([b.volume or 0.0 for b in bs], dtype=float)
        ind = {"close": c, "raw_close": c, "dv20": S.sma(c * v, 20)}  # all S.tradable reads; stored bars are split-adjusted
        with np.errstate(invalid="ignore"):
            liquid = (c >= S.MIN_PRICE) & (ind["dv20"] >= S.MIN_DOLLAR_VOLUME)
        out[t] = ({b.day: i for i, b in enumerate(bs)}, S.tradable(ind), ind["dv20"], S.momentum(c), liquid)
    return out


def coverage(prep: Prep, d: date) -> tuple[int, int]:
    """(names with their 12-1 return at day d, liquid names trading that day)."""
    ready = liquid = 0
    for idx, trad, _dv, m, liq in prep.values():
        i = idx.get(d)
        if i is None:
            continue
        liquid += bool(liq[i])
        ready += bool(trad[i] and m[i] == m[i])
    return ready, liquid


def ready_names(prep: Prep, d: date) -> int:
    return coverage(prep, d)[0]


def rank_at(prep: Prep, d: date, min_names: int = MIN_NAMES) -> tuple[list[tuple[str, float]], set[str]] | None:
    """The month-end ranking at day d: (top 20 with their 12-1 return, the top-40 keep set); None while too few names
    have a year of bars there (fewer than ``min_names``, or under MIN_COVERAGE of the liquid names trading)."""
    ready, liquid = coverage(prep, d)
    if ready < min_names or ready < MIN_COVERAGE * liquid:
        return None
    dv: dict[str, float] = {}
    mom: dict[str, float] = {}
    for t, (idx, trad, dv20, m, _liq) in prep.items():
        i = idx.get(d)
        if i is None or not trad[i]:
            continue
        dv[t] = float(dv20[i])
        if m[i] == m[i]:
            mom[t] = float(m[i])
    thr = S.cap_threshold(np.array(list(dv.values())))
    ranked = S.momentum_ranks({t: x for t, x in mom.items() if dv[t] >= thr})
    return [(t, mom[t]) for t in ranked[: S.MOM_TOP]], set(ranked[: S.MOM_KEEP])


def matches(rec: Mapping[str, Any], prep: Prep) -> bool:
    """A recorded ranking still describes today's data: each of its names that has a bar at that day gives the same
    12-1 return (a name with no data now — delisted since — cannot contradict it)."""
    d = date.fromisoformat(rec["day"])
    for t, m in rec["top"]:
        e = prep.get(t)
        if e is None:
            continue
        i = e[0].get(d)
        if i is None:
            continue
        now = float(e[3][i])
        if now != now or abs(now - m) > 1e-6 * max(1.0, abs(m)):
            return False
    return True


def rebalance(held: list[str], top: list[tuple[str, float]], keep: set[str], sector: Mapping[str, str]) -> tuple[list[str], list[str], list[str]]:
    """One month-end of the backtest's book: sell what left the top 40, fill free slots from the top 20 in rank order
    (skipping a sector that already has 5). Returns (new holdings, bought, sold)."""
    sold = [t for t in held if t not in keep]
    now = [t for t in held if t in keep]
    bought: list[str] = []
    for t, _m in top:
        if len(now) >= S.MOM_TOP:
            break
        if t in now:
            continue
        sec = sector.get(t, "Unknown")
        if sum(1 for x in now if sector.get(x, "Unknown") == sec) >= 5:
            continue
        now.append(t)
        bought.append(t)
    return now, bought, sold


class MomentumBook:
    def __init__(self, bars_all: Callable[[date, date], dict[str, list[Bar]]], now: Callable[[], datetime], data_dir: Path,
                 universe: Callable[[], dict[str, str]], results_path: Path | None = None, min_names: int = MIN_NAMES,
                 mode: str = "", exclude: Callable[[], set[str]] | None = None) -> None:
        self._min_names = min_names
        self._bars_all = bars_all
        self._now = now
        self._universe = universe  # ticker → sector of the app's known stocks (a name not in it ranks with sector Unknown)
        self._exclude = exclude or (lambda: set())  # ETFs and other non-stocks among the stored bars
        self._log = Path(data_dir) / (f"momentum_forward_{mode.lower()}.jsonl" if mode else "momentum_forward.jsonl")
        self._mode = mode
        self._results_path = results_path
        self._lock = threading.Lock()

    def validation(self) -> dict[str, Any]:
        v = {}
        p = self._results_path
        if p is not None and p.exists():
            try:
                v = (json.loads(p.read_text(encoding="utf-8")).get("variants") or {}).get("M-NF") or {}
            except (OSError, ValueError):
                v = {}
        bt = {k: v.get(k) for k in ("cagr", "mdd", "vol", "sharpe", "matched", "stress_cagr", "halves", "trades")} if v else None
        return {"status": "OWNER", "status_ko": "직접 선택 · 백테스트 기준 미달 · 전진 모의운영 중",
                "checks": (v.get("verdict") or {}).get("checks"), "backtest": bt, "spy_cagr": 0.15197548749154421 if v else None,
                "risks": list(RISK_KO)}

    def book(self) -> dict[str, Any]:
        now = self._now()
        session = last_completed_session(now)
        bars = self._bars_all(BOOK_FROM, now.date())
        sector = self._universe()
        skip = self._exclude() | {"SPY"}
        names = [t for t in bars if t not in skip]
        spy_days = sorted(b.day for b in bars.get("SPY", ()) if b.day <= session)
        ends = [d for d in spy_days if month_end(d)]
        prep = prepare(bars, names, session)
        recs = {r["day"]: r for r in self._read()}
        held: list[str] = []
        last: dict[str, Any] | None = None
        since: dict[str, str] = {}
        momentum: dict[str, float] = {}
        for d in ends:
            rec = recs.get(d.isoformat())
            if rec is not None and matches(rec, prep):
                top, keep = [(t, float(m)) for t, m in rec["top"]], set(rec["keep"])  # the month's ranking as first seen
            else:
                r = rank_at(prep, d, self._min_names)
                if r is None:
                    continue
                top, keep = r
                if d > CHOSEN_ON and d == ends[-1] and rec is None:
                    self._record(d, top, keep, now)
            held, bought, sold = rebalance(held, top, keep, sector)
            for t in bought:
                since[t] = d.isoformat()
            momentum = dict(top)
            last = {"day": d, "top": top, "keep": keep, "bought": bought, "sold": sold}
        base = {"strategy": SPEC.id, "name": SPEC.name, "version": SPEC.version, "entry": list(SPEC.entry), "exit": list(SPEC.exit),
                "validation": self.validation(), "session": session.isoformat(), "computed_at": now.isoformat(), "mode": self._mode or None,
                "next_rebalance": self._next_end(session).isoformat(), "next_execute": next_trading_day(self._next_end(session)).isoformat(),
                "forward": self.forward(bars, sector, session, prep)}
        latest = ends[-1] if ends else None
        if last is None or latest is None or last["day"] != latest:
            # never show an older month as the current list: the latest month-end is the only one that is current
            ready, liquid = coverage(prep, latest) if latest is not None else (0, 0)
            when = latest.isoformat() if latest is not None else "최근 월말"
            need = max(self._min_names, int(np.ceil(MIN_COVERAGE * liquid)))
            return base | {"state": "HELD", "holdings": [], "bought": [], "sold": [], "latest_month_end": when, "ready_names": ready,
                           "reasons": [f"{when} 순위를 낼 자료가 아직 부족합니다 — 1년({S.MOM_LOOKBACK + 1}거래일) 일봉을 가진 종목 {ready}개, {need}개 이상 필요"
                                       f"(그날 거래된 유동 종목 {liquid}개의 {MIN_COVERAGE:.0%})",
                                       "앱이 과거 일봉을 더 받으면(자동 동기화, 하루 몇 차례) 저절로 계산합니다."]}
        d = last["day"]
        rank = {t: k + 1 for k, (t, _m) in enumerate(last["top"])}
        rows = [{"ticker": t, "sector": sector.get(t, "Unknown"), "rank": rank.get(t), "momentum": momentum.get(t), "since": since.get(t),
                 "in_top20": t in rank} for t in held]
        rows.sort(key=lambda x: (x["rank"] or 99, x["ticker"]))
        return base | {"state": "READY", "rebalance_day": d.isoformat(), "execute_day": next_trading_day(d).isoformat(),
                       "holdings": rows, "bought": last["bought"], "sold": last["sold"],
                       "note": "월말 종가 순위로 정한 목록입니다. 실제 매매는 직접 판단하세요 — 앱은 주문하지 않습니다. 이 목록은 상승 확률이 아닙니다."}

    @staticmethod
    def _next_end(session: date) -> date:
        d = session + timedelta(days=1)
        while not month_end(d):
            d += timedelta(days=1)
        return d

    # ------------------------------------------------------------------ the forward record
    def _read(self) -> list[dict[str, Any]]:
        if not self._log.exists():
            return []
        out = []
        for line in self._log.read_text(encoding="utf-8").splitlines():
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
        return out

    def _record(self, d: date, top: list[tuple[str, float]], keep: set[str], now: datetime) -> None:
        seen = now.astimezone(timezone.utc)
        timely = seen <= session_open_utc(next_trading_day(d))
        with self._lock:
            if any(r.get("day") == d.isoformat() for r in self._read()):
                return  # one ranking per month-end, never rewritten
            self._log.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"version": SPEC.version, "mode": self._mode or None, "day": d.isoformat(), "top": [[t, m] for t, m in top],
                                    "keep": sorted(keep), "recorded_at": seen.isoformat(), "timely": timely}, ensure_ascii=False) + "\n")

    def forward(self, bars: Mapping[str, list[Bar]], sector: Mapping[str, str], session: date, prep: Prep | None = None) -> dict[str, Any]:
        """The paper account of the rankings seen before their next open: the backtest's own engine (next-open fills,
        costs, sector cap) on the stored bars. Late or other-data records are counted, never filled."""
        from marketlens.backtest import portfolio as P

        note = ("전진 모의운영: 선택 이후 월말 순위를 그 시점에 기록하고, 다음 거래일 시가 전에 기록된 것만 저장된 일봉으로 백테스트와 같은 방식"
                "(다음 날 시가, 비용 0.15%/회)으로 모의 체결합니다. 배당 미포함 가격 기준이며 실제 주문·실거래 성과가 아닙니다.")
        allr = sorted(self._read(), key=lambda r: r["day"])
        p = prep if prep is not None else prepare(bars, [t for t in bars if t != "SPY"], session)
        good = [r for r in allr if r.get("timely") and matches(r, p)]
        late = sum(1 for r in allr if not r.get("timely"))
        other = sum(1 for r in allr if r.get("timely") and not matches(r, p))
        counts = {"late_records": late, "other_data_records": other}
        if not good:
            return {"started": None, "note": note, "first_rebalance": self._next_end(max(session, CHOSEN_ON)).isoformat(), "open": []} | counts
        start = date.fromisoformat(good[0]["day"])
        names = {t for r in good for t, _m in r["top"]}
        spy_bars = [b for b in bars.get("SPY", ()) if start <= b.day <= session]
        if len(spy_bars) < 2:
            return {"started": start.isoformat(), "note": note, "pending": True, "open": []} | counts

        def sec(key: str, bs: list[Bar], sect: str) -> Any:
            return P.make_sec(key, key, sect, [b.day for b in bs], [(b.open, b.high, b.low, b.close, b.volume or 0.0) for b in bs], [])

        secs = {t: sec(t, [b for b in bars.get(t, ()) if b.day <= session], sector.get(t, "Unknown")) for t in names if bars.get(t)}
        spy = sec("SPY", [b for b in bars["SPY"] if b.day <= session], "ETF")
        cal = [b.day for b in spy_bars]
        tops = {date.fromisoformat(r["day"]): [(m, t) for t, m in r["top"]] for r in good}
        keeps = {date.fromisoformat(r["day"]): set(r["keep"]) for r in good}

        def xexit(_strategy: str, key: str, d: date) -> str | None:
            return None if d not in keeps else (None if key in keeps[d] else "rule")

        variant = next(v for v in P.S20_VARIANTS if v.id == "M-NF")
        res = P.simulate(variant, secs, spy, cal, {"MN": tops}, P.COST, start, session, xexit=xexit)
        eq = res["equity"]
        spy_ret = spy_bars[-1].close / spy_bars[0].close - 1
        return {"started": start.isoformat(), "through": session.isoformat(), "note": note,
                "measured_from": eq[0][0].isoformat() if eq else None, "first_rankings": [r["day"] for r in good[:3]],
                "return": eq[-1][1] / eq[0][1] - 1 if eq else None, "spy_price_return": spy_ret,
                "max_drawdown": P.series_stats(eq).get("max_drawdown") if len(eq) > 1 else None,
                "closed_trades": len(res["trades"]), "open": [k for k, _s, _d, _st in res["open"]],
                "invested": res["invested"][-1] if res["invested"] else 0.0} | counts
