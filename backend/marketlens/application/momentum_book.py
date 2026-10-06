"""대형주 모멘텀 (M-NF-1.0) in the app — the owner's choice of 2026-10-06 ("리스크없는 돈벌이는 없어 대형주 모멘텀으로
간다") after PREREGISTRATION §20, where it beat SPY on return but failed two of the six checks (Sharpe, concentration).
It is shown with that verdict and its risks, never as a verified strategy, and the app places no orders.

The SAME rules as the backtest (domain/strategies.py: momentum, month_ends via the market calendar, cap_threshold,
momentum_ranks; the monthly book of backtest/portfolio.py): at each month-end close, rank the day's large caps (top 500
by 20-session dollar volume inside the common universe) by 12-1 month return; hold the top 20 (5 % each, at most 5 per
sector); keep a holding while it stays inside the top 40 and the large caps; trade at the next session's open.

The forward record (data_dir/momentum_forward.jsonl, append-only) stores each month-end's ranking from the first
month-end after the owner's choice. Its paper results are computed with the backtest's own account (portfolio.simulate:
next-open fills, 0.15 % a side, dividends, delisting) on the stored daily bars — a record going forward, never a real
trade and never back-filled before the choice.
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
from marketlens.domain.market_calendar import is_trading_day, last_completed_session, next_trading_day

CHOSEN_ON = date(2026, 10, 6)  # the owner's choice: the forward record starts at the first month-end after it
HISTORY_DAYS = 420  # calendar days of bars read: 252 sessions of momentum + the 20-session dollar volume
SPEC = S.StrategySpec(
    "MN", "대형주 모멘텀", "M-NF-1.0",
    entry=("매달 마지막 거래일 종가에 대형주(그날 20일 평균 거래대금 상위 500) 안에서 12개월 전→1개월 전 수익률 상위 20위",
           "다음 거래일 시가에 빈 칸을 채움 · 20칸 × 5% · 업종당 최대 5종목"),
    exit=("월말 순위가 40위 밖이거나 대형주에서 빠지면 다음 거래일 시가에 매도", "시장 필터·손절·목표가 없음"),
    max_hold=None, needs_spy=False)
RISK_KO = ("백테스트(2017~2026)에서 최대 낙폭 −61%, 변동성 SPY의 2.4배였습니다. 몇 달 연속 크게 잃을 수 있습니다.",
           "이익의 대부분이 소수 종목(CVNA·APP·RKLB·ASTS·SMCI)에서 나왔습니다. 그런 종목이 다시 나온다는 보장은 없습니다.",
           "2017~2023년만 보면 연 8.4%로 SPY(13.1%)보다 낮았습니다. 최근 3년의 급등주 장세에 크게 기댄 결과입니다.")


def month_end(d: date) -> bool:
    return is_trading_day(d) and next_trading_day(d).month != d.month


def prepare(bars: Mapping[str, list[Bar]], universe: Mapping[str, str], through: date) -> dict[str, tuple[dict[date, int], np.ndarray, np.ndarray, np.ndarray]]:
    """Per name, once: (day → index, tradable, 20-session dollar volume, 12-1 momentum) on bars up to ``through``."""
    out = {}
    for t in universe:
        bs = [b for b in bars.get(t, ()) if b.day <= through]
        if len(bs) <= S.MOM_LOOKBACK:
            continue
        c = np.array([b.close for b in bs], dtype=float)
        v = np.array([b.volume or 0.0 for b in bs], dtype=float)
        ind = {"close": c, "raw_close": c, "dv20": S.sma(c * v, 20)}  # all S.tradable reads; stored bars are split-adjusted
        out[t] = ({b.day: i for i, b in enumerate(bs)}, S.tradable(ind), ind["dv20"], S.momentum(c))
    return out


def rank_at(prep: Mapping[str, tuple[dict[date, int], np.ndarray, np.ndarray, np.ndarray]], d: date) -> tuple[list[tuple[str, float]], set[str]] | None:
    """The month-end ranking at day d: (top 20 with their 12-1 return, the top-40 keep set); None without a year of bars."""
    dv: dict[str, float] = {}
    mom: dict[str, float] = {}
    for t, (idx, trad, dv20, m) in prep.items():
        i = idx.get(d)
        if i is None or not trad[i]:
            continue
        dv[t] = float(dv20[i])
        if m[i] == m[i]:
            mom[t] = float(m[i])
    if not mom:
        return None
    thr = S.cap_threshold(np.array(list(dv.values())))
    ranked = S.momentum_ranks({t: x for t, x in mom.items() if dv[t] >= thr})
    return [(t, mom[t]) for t in ranked[: S.MOM_TOP]], set(ranked[: S.MOM_KEEP])


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
                 universe: Callable[[], dict[str, str]], results_path: Path | None = None) -> None:
        self._bars_all = bars_all
        self._now = now
        self._universe = universe  # ticker → sector of the app's stocks (ETFs excluded)
        self._log = Path(data_dir) / "momentum_forward.jsonl"
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
        bars = self._bars_all(session - timedelta(days=HISTORY_DAYS), now.date())
        sector = self._universe()
        spy_days = sorted(b.day for b in bars.get("SPY", ()) if b.day <= session)
        ends = [d for d in spy_days if month_end(d)]
        held: list[str] = []
        last: dict[str, Any] | None = None
        since: dict[str, str] = {}
        momentum: dict[str, float] = {}
        prep = prepare(bars, sector, session)
        for d in ends:
            r = rank_at(prep, d)
            if r is None:
                continue
            top, keep = r
            held, bought, sold = rebalance(held, top, keep, sector)
            for t in bought:
                since[t] = d.isoformat()
            momentum = dict(top)
            last = {"day": d, "top": top, "keep": keep, "bought": bought, "sold": sold}
            if d > CHOSEN_ON:
                self._record(d, top, keep)
        base = {"strategy": SPEC.id, "name": SPEC.name, "version": SPEC.version, "entry": list(SPEC.entry), "exit": list(SPEC.exit),
                "validation": self.validation(), "session": session.isoformat(), "computed_at": now.isoformat(),
                "next_rebalance": self._next_end(session).isoformat(), "next_execute": next_trading_day(self._next_end(session)).isoformat(),
                "forward": self.forward(bars, sector, session)}
        if last is None:
            n = max((len([b for b in bs if b.day <= session]) for t, bs in bars.items() if t in sector), default=0)
            return base | {"state": "HELD", "reasons": [f"월말 순위에 일봉 {S.MOM_LOOKBACK + 1}거래일 이상이 필요합니다 — 가장 긴 종목이 {n}거래일"],
                           "holdings": [], "bought": [], "sold": []}
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

    def _record(self, d: date, top: list[tuple[str, float]], keep: set[str]) -> None:
        with self._lock:
            if any(r.get("day") == d.isoformat() for r in self._read()):
                return  # one ranking per month-end, never rewritten
            self._log.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"version": SPEC.version, "day": d.isoformat(), "top": [[t, m] for t, m in top], "keep": sorted(keep),
                                    "recorded_at": self._now().astimezone(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")

    def forward(self, bars: Mapping[str, list[Bar]], sector: Mapping[str, str], session: date) -> dict[str, Any]:
        """The paper account of the recorded rankings: the backtest's own engine (next-open fills, costs, sector cap)."""
        from marketlens.backtest import portfolio as P

        recs = sorted(self._read(), key=lambda r: r["day"])
        note = "전진 모의운영: 선택 이후 월말 순위를 기록하고, 저장된 일봉으로 백테스트와 같은 방식(다음 날 시가, 비용 0.15%/회)으로 모의 체결합니다. 실제 주문·실거래 성과가 아닙니다."
        if not recs:
            return {"started": None, "note": note, "first_rebalance": self._next_end(max(session, CHOSEN_ON)).isoformat()}
        start = date.fromisoformat(recs[0]["day"])
        names = {t for r in recs for t, _m in r["top"]}
        spy_bars = [b for b in bars.get("SPY", ()) if start <= b.day <= session]
        if len(spy_bars) < 2:
            return {"started": start.isoformat(), "note": note, "pending": True}

        def sec(key: str, bs: list[Bar], sect: str) -> Any:
            return P.make_sec(key, key, sect, [b.day for b in bs], [(b.open, b.high, b.low, b.close, b.volume or 0.0) for b in bs], [])

        secs = {t: sec(t, [b for b in bars.get(t, ()) if b.day <= session], sector.get(t, "Unknown")) for t in names if bars.get(t)}
        spy = sec("SPY", [b for b in bars["SPY"] if b.day <= session], "ETF")
        cal = [b.day for b in spy_bars]
        tops = {date.fromisoformat(r["day"]): [(m, t) for t, m in r["top"]] for r in recs}
        keeps = {date.fromisoformat(r["day"]): set(r["keep"]) for r in recs}

        def xexit(_strategy: str, key: str, d: date) -> str | None:
            return None if d not in keeps else (None if key in keeps[d] else "rule")

        variant = next(v for v in P.S20_VARIANTS if v.id == "M-NF")
        res = P.simulate(variant, secs, spy, cal, {"MN": tops}, P.COST, start, session, xexit=xexit)
        eq = res["equity"]
        spy_ret = spy_bars[-1].close / spy_bars[0].close - 1
        return {"started": start.isoformat(), "through": session.isoformat(), "note": note,
                "return": eq[-1][1] / eq[0][1] - 1 if eq else None, "spy_price_return": spy_ret,
                "max_drawdown": P.series_stats(eq).get("max_drawdown") if len(eq) > 1 else None,
                "closed_trades": len(res["trades"]), "open": [k for k, _s, _d, _st in res["open"]], "invested": res["invested"][-1] if res["invested"] else 0.0}
