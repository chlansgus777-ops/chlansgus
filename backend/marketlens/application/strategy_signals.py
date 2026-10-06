"""The strategies' signals in the app (docs/strategies/STRATEGIES.md) — the SAME rules as the daily backtest
(domain/strategies.py); only the data supply (the app's stored daily bars, the live quote) and the fills differ.

- **확정 신호 (confirmed):** the rule met at a completed session's close (its bar stored after 16:00 New York). It is
  executed — in the forward paper log, never by the app — at the next session's open. Recorded once, never re-issued.
- **예비 신호 (preliminary):** during the regular session, the rule met if the session closed at the live price now. It
  can still disappear by the close; it never becomes an alert or a paper trade by itself.
- **보류 (held):** the data a strategy needs is missing (history, today's volume, SPY): no signal, the reason shown.

The forward paper log (data_dir/strategy_forward.jsonl) is append-only: one line per confirmed signal with the rule
version and the close it used. Its results are computed from the stored bars after the signal, the backtest's fills
(next open, the strategy's exit rule, 0.15 % a side) — a record that accumulates going forward, never a real trade.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

import numpy as np

from marketlens.domain import strategies as S
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import TradingSession, classify_session, last_completed_session, next_trading_day, session_close_utc, session_open_utc

log = logging.getLogger("marketlens.strategies")

COST = 0.0015  # a side, as the backtest
HISTORY_DAYS = 420
# a preliminary signal reads a regular-session trade this recent; an older or pre/after-market price holds it
# (independent review 0444a21 F07: an 8-hour-old premarket quote made a "current" preliminary signal)
PRELIM_MAX_AGE = timedelta(minutes=20)


def fresh_quote(q: tuple[float, datetime] | None, now: datetime) -> bool:
    return (q is not None and classify_session(q[1]) == TradingSession.REGULAR and timedelta(0) <= now - q[1] <= PRELIM_MAX_AGE)  # calendar days of bars read (≥ 252 sessions + the 200-day line)
# what counts as a stock: the instrument directory's kinds (providers/live/nasdaq_symbols.classify — the LIVE store keeps
# "common", "unknown", "preferred", "warrant", "unit", "right", "note", "etf") and older provider codes. Missing
# "common" here once kept every listed common stock out of the strategies in LIVE (owner 2026-10-06: "유동 종목 3개").
STOCK_KINDS = {"common", "unknown", "CS", "ADRC", "OS", "stock", "STOCK", ""}


def is_stock(kinds: Mapping[str, str], ticker: str) -> bool:
    """A name the directory does not list counts as a stock (unknown ≠ excluded); keys are the directory's spelling."""
    from marketlens.providers.live.nasdaq_symbols import canonical

    return not kinds or kinds.get(canonical(ticker), kinds.get(ticker, "unknown")) in STOCK_KINDS

STATUS_KO = {"VERIFYING": "검증 중", "NOT_ADOPTED": "미채택", "FORWARD": "백테스트 통과 · 전진 모의운영 중"}


@dataclass(frozen=True)
class Arrays:
    days: list[date]
    ind: dict[str, np.ndarray]


def arrays(bars: list[Bar]) -> Arrays:
    """Indicators of stored bars (already on today's share basis: the sync adjusts them for splits)."""
    o = np.array([b.open for b in bars], dtype=float)
    h = np.array([b.high for b in bars], dtype=float)
    lo = np.array([b.low for b in bars], dtype=float)
    c = np.array([b.close for b in bars], dtype=float)
    v = np.array([b.volume or 0.0 for b in bars], dtype=float)
    return Arrays([b.day for b in bars], S.indicators(o, h, lo, c, v, c))


def strategy_status(results: Mapping[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Each strategy's verification status from the committed backtest result (config/strategy_results.json)."""
    out: dict[str, dict[str, Any]] = {}
    for sid in S.STRATEGIES:
        v = ((results or {}).get("variants") or {}).get(sid)
        if not v or "verdict" not in v:
            out[sid] = {"status": "VERIFYING", "status_ko": STATUS_KO["VERIFYING"], "backtest": None}
            continue
        passed = bool(v["verdict"].get("passed"))
        st = "FORWARD" if passed else "NOT_ADOPTED"
        full = v.get("full") or {}
        tr = full.get("trades") or {}
        out[sid] = {"status": st, "status_ko": STATUS_KO[st], "checks": v["verdict"].get("checks"),
                    "backtest": {"cagr": full.get("cagr"), "max_drawdown": full.get("max_drawdown"), "sharpe": full.get("sharpe"),
                                 "exposure": full.get("exposure"), "trades": tr.get("trades"), "win_rate": tr.get("win_rate"),
                                 "expectancy": tr.get("expectancy"), "avg_sessions": tr.get("avg_sessions"),
                                 "matched_spy_cagr": (v.get("exposure_matched_spy") or {}).get("cagr"),
                                 "stress_expectancy": ((v.get("stress_cost") or {}).get("trades") or {}).get("expectancy")}}
    return out


def _signal_row(spec: S.StrategySpec, ticker: str, a: Arrays, i: int, kind: str, spy_up: bool | None, signal_day: date,
                price_ts: datetime | None) -> dict[str, Any]:
    execute = next_trading_day(signal_day)
    return {
        "strategy": spec.id, "name": spec.name, "version": spec.version, "ticker": ticker, "kind": kind,
        "kind_ko": "확정 신호" if kind == "confirmed" else "예비 신호",
        "signal_day": signal_day.isoformat(), "close": float(a.ind["close"][i]),
        "conditions": S.explain_entry(spec.id, a.ind, i, spy_up),
        "execute_at": f"{execute.isoformat()} 시가 (미국 동부)" if kind == "confirmed" else "오늘 종가로 확정되면 다음 거래일 시가",
        # the open itself as an instant: the screen compares it with now, not a date with the UTC date
        "execute_ts": session_open_utc(execute).isoformat() if kind == "confirmed" else None,
        "exit": list(spec.exit), "max_hold": spec.max_hold,
        "price_ts": price_ts.isoformat() if price_ts else None, "bars_through": a.days[i].isoformat() if kind == "confirmed" else a.days[-1].isoformat(),
    }


class StrategySignals:
    def __init__(self, bars_all: Callable[[date, date], dict[str, list[Bar]]], now: Callable[[], datetime], data_dir: Path,
                 kinds: Callable[[], dict[str, str]] | None = None, results_path: Path | None = None, mode: str = "") -> None:
        self._bars_all = bars_all
        self._now = now
        self._kinds = kinds or (lambda: {})
        # one journal per data mode: a MOCK signal never enters the LIVE record (independent review 0444a21 F02)
        self._log = Path(data_dir) / (f"strategy_forward_{mode.lower()}.jsonl" if mode else "strategy_forward.jsonl")
        self._results_path = results_path
        self._lock = threading.Lock()
        self._confirmed_cache: dict[str, tuple[Any, list[dict[str, Any]], list[tuple[str, str]]]] = {}

    # ------------------------------------------------------------------ status
    def results(self) -> dict[str, Any] | None:
        p = self._results_path
        if p is None or not p.exists():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def status(self) -> dict[str, dict[str, Any]]:
        return strategy_status(self.results())

    # ------------------------------------------------------------------ the market
    @staticmethod
    def _confirmed_one(t: str, done: list[Bar], session: date, spy: Any, spy_up_by_day: Mapping[date, bool]
                       ) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
        """One name at the last completed session: its confirmed signal rows and the data holds per strategy."""
        a = arrays(done)
        i = len(a.days) - 1
        rows: list[dict[str, Any]] = []
        holds: list[tuple[str, str]] = []
        for sid, spec in S.STRATEGIES.items():
            why = S.data_holds(sid, len(a.days), bool(done[-1].volume), len(spy.days) if spy else None)
            if why:
                for w in why:
                    holds.append((sid, w.split(" — ")[0] if "SPY" in w or "거래량" in w else f"일봉 {S.MIN_HISTORY}거래일 미만"))
                continue
            up = np.array([spy_up_by_day.get(d, False) for d in a.days]) if sid == "C" else None
            if S.signals(sid, a.ind, up)[i]:
                row = _signal_row(spec, t, a, i, "confirmed", spy_up_by_day.get(session), session, session_close_utc(session))
                pr = S.priority(sid, a.ind)[i]
                row["priority"] = float(pr) if pr == pr else 0.0
                rows.append(row)
        return rows, holds

    def scan(self, live: Callable[[str], tuple[float, datetime] | None] | None = None) -> dict[str, Any]:
        """Confirmed signals at the last completed session, preliminary ones at the live price (regular session only),
        and how many names each strategy had to hold for missing data."""
        now = self._now()
        session = last_completed_session(now)
        bars = self._bars_all(session - timedelta(days=HISTORY_DAYS), now.date())
        spy_bars = bars.get("SPY") or []
        spy = arrays(spy_bars) if len(spy_bars) >= 200 else None
        spy_up_by_day = {d: bool(spy.ind["close"][k] > spy.ind["sma200"][k]) for k, d in enumerate(spy.days)} if spy else {}
        kinds = self._kinds()
        confirmed: list[dict[str, Any]] = []
        prelim: list[dict[str, Any]] = []
        held: dict[str, dict[str, int]] = {sid: {} for sid in S.STRATEGIES}
        names = 0
        stale = 0
        spy_key = (len(spy.days), spy.days[-1], float(spy.ind["close"][-1])) if spy else None
        fresh_cache: dict[str, tuple[Any, list[dict[str, Any]], list[tuple[str, str]]]] = {}
        for t, bs in bars.items():
            if t == "SPY" or not is_stock(kinds, t):
                continue
            done = [b for b in bs if b.day <= session]
            if not done:
                continue
            names += 1
            if done[-1].day != session:  # no bar for the last session: a halted, delisted or not yet synced name
                stale += 1
                continue
            # a name's confirmed result depends only on its own completed bars and SPY's: kept from the last round while
            # neither changed (the market-wide pass took ~4 s every 10 minutes for the same answer; owner 2026-10-06)
            ck = (session, len(done), done[0].day, done[-1], spy_key)
            hit = self._confirmed_cache.get(t)
            if hit is not None and hit[0] == ck:
                rows, holds = hit[1], hit[2]
            else:
                rows, holds = self._confirmed_one(t, done, session, spy, spy_up_by_day)
                fresh_cache[t] = (ck, rows, holds)
            if hit is not None and hit[0] == ck:
                fresh_cache[t] = hit
            for sid, key in holds:
                held[sid][key] = held[sid].get(key, 0) + 1
            confirmed.extend(dict(r) for r in rows)
            # preliminary (A only): today's live price as if it were the close; C needs the day's volume, which a
            # quote does not carry — it is held, never guessed
            if live is not None and now.date() > session:
                q = live(t)
                if fresh_quote(q, now):
                    px, ts = q
                    pa = arrays(done + [Bar(now.date(), px, px, px, px, 0.0)])
                    j = len(pa.days) - 1
                    if not S.data_holds("A", len(pa.days), True, None) and S.signals("A", pa.ind)[j]:
                        prelim.append(_signal_row(S.A, t, pa, j, "preliminary", None, now.date(), ts))
        self._confirmed_cache = fresh_cache  # only the names seen this round (a dropped name leaves no stale entry)
        confirmed.sort(key=lambda r: (r["strategy"], -r.get("priority", 0.0), r["ticker"]))
        self._record(confirmed)
        status = self.status()
        for r in confirmed + prelim:
            r["validation"] = status[r["strategy"]]
        return {"session": session.isoformat(), "computed_at": now.isoformat(), "names": names, "names_without_last_bar": stale,
                "spy_up": spy_up_by_day.get(session), "confirmed": confirmed, "preliminary": prelim, "held": held,
                "strategies": [{"id": s.id, "name": s.name, "version": s.version, "entry": list(s.entry), "exit": list(s.exit),
                                "max_hold": s.max_hold} | status[s.id] for s in S.STRATEGIES.values()],
                "preliminary_note": "예비 신호는 장중 가격으로 계산한 것이라 종가에 사라질 수 있습니다. 알림·모의 체결에 쓰지 않습니다. C는 장중 거래량이 없어 예비 판단을 하지 않습니다."}

    def ticker(self, ticker: str, live: tuple[float, datetime] | None = None) -> dict[str, Any]:
        """One name: each strategy's state now (signal, no signal with the unmet conditions, or held with the reason)."""
        now = self._now()
        session = last_completed_session(now)
        t = ticker.upper()
        bars = self._bars_all(session - timedelta(days=HISTORY_DAYS), now.date())
        done = [b for b in bars.get(t, []) if b.day <= session]
        spy_bars = [b for b in bars.get("SPY", []) if b.day <= session]
        spy = arrays(spy_bars) if len(spy_bars) >= 200 else None
        spy_up_by_day = {d: bool(spy.ind["close"][k] > spy.ind["sma200"][k]) for k, d in enumerate(spy.days)} if spy else {}
        status = self.status()
        out: list[dict[str, Any]] = []
        for sid, spec in S.STRATEGIES.items():
            base = {"strategy": sid, "name": spec.name, "version": spec.version, "exit": list(spec.exit), "max_hold": spec.max_hold, "validation": status[sid]}
            if not done or done[-1].day != session:
                out.append(base | {"state": "HELD", "reasons": [f"{session.isoformat()} 일봉 없음 — 거래 정지·누락이거나 아직 받지 못함"]})
                continue
            why = S.data_holds(sid, len(done), bool(done[-1].volume), len(spy.days) if spy else None)
            if why:
                out.append(base | {"state": "HELD", "reasons": why})
                continue
            a = arrays(done)
            i = len(a.days) - 1
            up = np.array([spy_up_by_day.get(d, False) for d in a.days]) if sid == "C" else None
            sig = bool(S.signals(sid, a.ind, up)[i])
            tradable = bool(S.tradable(a.ind)[i])
            row = base | {"state": "SIGNAL" if sig else "NONE", "session": session.isoformat(), "bars_through": a.days[i].isoformat(),
                          "conditions": S.explain_entry(sid, a.ind, i, spy_up_by_day.get(session)), "tradable": tradable,
                          "execute_at": next_trading_day(session).isoformat() if sig else None}
            if not tradable:
                row["reasons"] = [f"거래 대상 아님 — 종가 ${S.MIN_PRICE:g} 이상·20일 평균 거래대금 $2,000만 이상이어야 함"]
            if sid == "A" and fresh_quote(live, now) and now.date() > session:
                pa = arrays(done + [Bar(now.date(), live[0], live[0], live[0], live[0], 0.0)])
                row["preliminary"] = bool(S.signals("A", pa.ind)[len(pa.days) - 1])
                row["preliminary_price_ts"] = live[1].isoformat()
            out.append(row)
        return {"ticker": t, "session": session.isoformat(), "strategies": out,
                "note": "가격은 장 마감 종가 기준으로 판정합니다. 재무·뉴스 분석과 따로 계산합니다."}

    # ------------------------------------------------------------------ the forward paper log
    def _record(self, confirmed: list[dict[str, Any]]) -> None:
        if not confirmed:
            return
        with self._lock:
            seen = {(r["strategy"], r["ticker"], r["signal_day"]) for r in self._read()}
            new = [r for r in confirmed if (r["strategy"], r["ticker"], r["signal_day"]) not in seen]
            if not new:
                return
            self._log.parent.mkdir(parents=True, exist_ok=True)
            with open(self._log, "a", encoding="utf-8") as f:
                for r in new:
                    f.write(json.dumps({"strategy": r["strategy"], "version": r["version"], "ticker": r["ticker"], "signal_day": r["signal_day"],
                                        "close": r["close"], "recorded_at": self._now().astimezone(timezone.utc).isoformat()}, ensure_ascii=False) + "\n")

    def _read(self) -> list[dict[str, Any]]:
        if not self._log.exists():
            return []
        out = []
        with open(self._log, encoding="utf-8") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
        return out

    def forward(self) -> dict[str, Any]:
        """The forward paper trades of the logged signals, filled from the stored bars after each signal (next open,
        the strategy's own exit, costs both sides). Open ones are marked to the last close. Never a real trade."""
        sigs = self._read()
        now = self._now()
        session = last_completed_session(now)
        trades: list[dict[str, Any]] = []
        if sigs:
            first = min(date.fromisoformat(s["signal_day"]) for s in sigs)
            bars = self._bars_all(first - timedelta(days=HISTORY_DAYS), now.date())
            for s in sigs:
                trades.append(self._paper(s, [b for b in bars.get(s["ticker"], []) if b.day <= session]))
        closed = [t for t in trades if t["state"] == "CLOSED"]
        by: dict[str, dict[str, Any]] = {}
        for sid in S.STRATEGIES:
            c = [t for t in closed if t["strategy"] == sid]
            rets = [t["ret"] for t in c]
            by[sid] = {"signals": sum(1 for t in trades if t["strategy"] == sid), "closed": len(c),
                       "open": sum(1 for t in trades if t["strategy"] == sid and t["state"] == "OPEN"),
                       "win_rate": (sum(1 for r in rets if r > 0) / len(rets)) if rets else None,
                       "avg_ret": (sum(rets) / len(rets)) if rets else None}
        return {"started": min((s["signal_day"] for s in sigs), default=None), "session": session.isoformat(), "by_strategy": by,
                "trades": sorted(trades, key=lambda t: t["signal_day"], reverse=True)[:200],
                "note": "전진 모의운영: 규칙을 고정한 뒤 나온 신호를 저장된 일봉으로 모의 체결한 기록입니다. 실제 주문·실거래 성과가 아닙니다. 비용 0.15%/회 반영."}

    def _paper(self, s: Mapping[str, Any], bars: list[Bar]) -> dict[str, Any]:
        sid = s["strategy"]
        d0 = date.fromisoformat(s["signal_day"])
        base = {"strategy": sid, "version": s["version"], "ticker": s["ticker"], "signal_day": s["signal_day"]}
        k0 = next((k for k, b in enumerate(bars) if b.day > d0), None)
        if k0 is None:
            return base | {"state": "PENDING", "state_ko": "체결 대기(다음 거래일 시가)"}
        if k0 == 0 or bars[k0 - 1].day != d0:
            return base | {"state": "MISSED", "state_ko": "체결 불가(신호일 일봉 불일치)"}
        if bars[k0].day != next_trading_day(d0):
            return base | {"state": "MISSED", "state_ko": "체결 불가(다음 거래일 거래 없음)"}
        seen = s.get("recorded_at")
        if not seen or datetime.fromisoformat(seen) > session_open_utc(bars[k0].day):
            # first seen after the open it would have bought at: a replay of the past, never a forward fill
            # (independent review 0444a21 F04)
            return base | {"state": "MISSED", "state_ko": "체결 불가(다음 거래일 시가 이후에 처음 기록됨)", "recorded_at": seen}
        a = arrays(bars)
        entry = bars[k0].open
        held = 0
        for k in range(k0, len(bars)):
            held += 1
            why = S.exit_due(sid, a.ind, k, held)
            if why:
                if k + 1 < len(bars):
                    px = bars[k + 1].open
                    ret = px * (1 - COST) / (entry * (1 + COST)) - 1
                    return base | {"state": "CLOSED", "state_ko": "청산", "entry_day": bars[k0].day.isoformat(), "entry": entry,
                                   "exit_day": bars[k + 1].day.isoformat(), "exit": px, "ret": ret, "sessions": held, "reason": why}
                return base | {"state": "EXITING", "state_ko": "다음 거래일 시가 청산 예정", "entry_day": bars[k0].day.isoformat(), "entry": entry,
                               "last": bars[k].close, "ret_open": bars[k].close / (entry * (1 + COST)) - 1, "sessions": held, "reason": why}
        last = bars[-1]
        return base | {"state": "OPEN", "state_ko": "모의 보유 중", "entry_day": bars[k0].day.isoformat(), "entry": entry, "last": last.close,
                       "ret_open": last.close / (entry * (1 + COST)) - 1, "sessions": held}  # the entry cost paid (review F08)


def results_path() -> Path:
    from marketlens.config import CONFIG_DIR

    return Path(os.environ.get("MARKETLENS_STRATEGY_RESULTS", str(CONFIG_DIR / "strategy_results.json")))
