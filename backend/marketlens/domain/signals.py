"""Return signals (docs/backtest/PREREGISTRATION.md §13) — pure and deterministic, fixed before the 2016+ results.

Four signals with published evidence, each rebuilt on data MarketLens has at the analysis time (no future bar, no
estimate the past did not have), so the backtest can judge them on the same code:

1. 52-week-high proximity (George & Hwang 2004): last close ÷ highest close of the last 252 sessions.
2. Relative-strength rank (IBD style): 0.4×63d + 0.2×126d + 0.2×189d + 0.2×252d return, as a percentile of the
   day's stage-1 universe.
3. F-score, MarketLens edition: Piotroski's nine tests on the fields the SEC gives MarketLens (ROE for ROA, cash ÷
   debt for the current ratio — total assets are not ingested).
4. Earnings-announcement drift, price based: the stock's return over the announcement window minus SPY's, while
   the release is at most 60 sessions old (EPS surprise is not used: the past consensus is not available).

Each signal returns its value, a 0..1 sub-score and a Korean sentence; ``None`` when it cannot be computed — never a
guess. The component is the plain average of the available sub-scores (at least 2), fixed weights.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime
from typing import Sequence

from marketlens.domain.earnings import EarningsReport
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import to_ny

YEAR = 252
RS_WINDOWS = ((63, 0.4), (126, 0.2), (189, 0.2), (252, 0.2))
EAR_MAX_AGE = 60  # sessions after the announcement window
MIN_FSCORE_TESTS = 7
MIN_SIGNALS = 2


def lin(x: float, bad: float, good: float) -> float:
    return max(0.0, min(1.0, (x - bad) / (good - bad)))


@dataclass(frozen=True, slots=True)
class Signal:
    key: str  # high52 | rs_rank | fscore | ear
    value: float | None
    sub: float | None  # 0..1
    text: str  # Korean, with the numbers
    detail: tuple[str, ...] = ()  # e.g. the F-score tests passed / failed


# ------------------------------------------------------------------ 1. 52-week high
def high52(bars: Sequence[Bar]) -> Signal:
    if len(bars) < YEAR:
        return Signal("high52", None, None, f"52주 신고가 근접도: 가격 이력 {len(bars)}일로 252거래일 미만 — 계산 안 함")
    window = bars[-YEAR:]
    top = max(b.close for b in window)
    if top <= 0:
        return Signal("high52", None, None, "52주 신고가 근접도: 가격 오류")
    v = window[-1].close / top
    near = "신고가 부근" if v >= 0.95 else "고점 대비 조정 중" if v >= 0.8 else "고점에서 멀리 떨어짐"
    return Signal("high52", round(v, 4), round(lin(v, 0.70, 0.98), 4), f"52주 최고 종가의 {v:.0%} — {near}")


# ------------------------------------------------------------------ 2. relative strength rank
def rs_raw(closes: Sequence[float]) -> float | None:
    """IBD-style weighted 12-month return; None below 253 closes or with a non-positive close."""
    if len(closes) <= YEAR or closes[-1] <= 0:
        return None
    total = 0.0
    for n, w in RS_WINDOWS:
        base = closes[-1 - n]
        if base <= 0:
            return None
        total += w * (closes[-1] / base - 1)
    return total


def percentile(universe_sorted: Sequence[float], x: float) -> float:
    """Share of the universe at or below ``x`` (ties count half) — 0..1."""
    n = len(universe_sorted)
    if n == 0:
        return 0.5
    lo, hi = bisect_left(universe_sorted, x), bisect_right(universe_sorted, x)
    return (lo + 0.5 * (hi - lo)) / n


def rs_rank(bars: Sequence[Bar], pct: float | None, universe_size: int) -> Signal:
    raw = rs_raw([b.close for b in bars])
    if raw is None:
        return Signal("rs_rank", None, None, "상대강도 순위: 가격 이력 1년 미만 — 계산 안 함")
    if pct is None:
        return Signal("rs_rank", None, None, "상대강도 순위: 비교할 스캔 종목 분포가 아직 없음(다음 스캔 뒤 계산)")
    rank = max(1, min(99, round(pct * 99)))
    word = "시장 주도주" if rank >= 80 else "시장 평균 이상" if rank >= 50 else "시장보다 약함"
    return Signal("rs_rank", float(rank), round(pct, 4), f"상대강도 {rank}/99 (1년 가중 수익 {raw:+.0%}, {universe_size:,}개 종목 대비) — {word}")


# ------------------------------------------------------------------ 3. F-score (MarketLens edition)
def _ttm(qs: Sequence[QuarterlyFinancials], end: int, f: str) -> float | None:
    """Sum of field ``f`` over the four quarters ending at index ``end`` (all four must exist)."""
    if end < 3:
        return None
    vals = [getattr(q, f) for q in qs[end - 3:end + 1]]
    return None if any(v is None for v in vals) else float(sum(vals))


def _ratio(a: float | None, b: float | None) -> float | None:
    return None if a is None or b is None or b <= 0 else a / b


def fscore(quarters: Sequence[QuarterlyFinancials]) -> Signal:
    qs = sorted(quarters, key=lambda q: q.period_end)
    if len(qs) < 4:
        return Signal("fscore", None, None, f"F-스코어: 분기 재무 {len(qs)}개 — 4개 미만이라 계산 안 함")
    now, ago = len(qs) - 1, len(qs) - 5
    has_ago = ago >= 3
    cur, old = qs[now], qs[ago] if ago >= 0 else None
    ni, cfo, rev, gp = (_ttm(qs, now, f) for f in ("net_income", "operating_cash_flow", "revenue", "gross_profit"))
    ni0, rev0, gp0 = ((_ttm(qs, ago, f) if has_ago else None) for f in ("net_income", "revenue", "gross_profit"))
    eq, eq0 = cur.total_equity, old.total_equity if old else None
    tests: list[tuple[str, bool | None]] = [
        ("순이익 흑자(TTM)", None if ni is None else ni > 0),
        ("영업현금흐름 흑자(TTM)", None if cfo is None else cfo > 0),
        ("ROE 개선(1년 전 대비)", None if _ratio(ni, eq) is None or _ratio(ni0, eq0) is None else _ratio(ni, eq) > _ratio(ni0, eq0)),  # type: ignore[operator]
        ("현금흐름이 이익보다 큼(이익의 질)", None if ni is None or cfo is None else cfo > ni),
        ("부채비율 하락", None if _ratio(cur.total_debt, eq) is None or old is None or _ratio(old.total_debt, eq0) is None
         else _ratio(cur.total_debt, eq) < _ratio(old.total_debt, eq0)),  # type: ignore[operator]
        ("현금÷부채 상승", None if old is None or _ratio(cur.cash, cur.total_debt) is None or _ratio(old.cash, old.total_debt) is None
         else _ratio(cur.cash, cur.total_debt) > _ratio(old.cash, old.total_debt)),  # type: ignore[operator]
        ("주식 수 희석 없음", None if old is None or cur.shares_diluted is None or old.shares_diluted is None or old.shares_diluted <= 0
         else cur.shares_diluted <= old.shares_diluted * 1.01),
        ("매출총이익률 상승", None if _ratio(gp, rev) is None or _ratio(gp0, rev0) is None else _ratio(gp, rev) > _ratio(gp0, rev0)),  # type: ignore[operator]
        ("자본 회전율(매출÷자기자본) 상승", None if _ratio(rev, eq) is None or _ratio(rev0, eq0) is None else _ratio(rev, eq) > _ratio(rev0, eq0)),  # type: ignore[operator]
    ]
    known = [(n, ok) for n, ok in tests if ok is not None]
    if len(known) < MIN_FSCORE_TESTS:
        return Signal("fscore", None, None, f"F-스코어: 계산 가능한 검사 {len(known)}/9개 — {MIN_FSCORE_TESTS}개 미만이라 계산 안 함")
    passed = sum(1 for _, ok in known if ok)
    sub = passed / len(known)
    word = "재무가 좋아지는 중" if sub >= 0.7 else "보통" if sub >= 0.45 else "재무가 나빠지는 중"
    detail = tuple(f"{'✓' if ok else '✗'} {n}" for n, ok in known) + tuple(f"– {n} (자료 없음)" for n, ok in tests if ok is None)
    return Signal("fscore", float(passed), round(sub, 4), f"F-스코어 {passed}/{len(known)} — {word}", detail)


# ------------------------------------------------------------------ 4. earnings-announcement drift (price based)
def _reaction_start(released_at: datetime | None, report_date: date) -> tuple[date, bool]:
    """(the first NY day whose close can react, whether the release came after that day's open)."""
    if released_at is None:
        return report_date, True
    ny = to_ny(released_at)
    after_close = (ny.hour, ny.minute) >= (16, 0)
    return ny.date(), after_close


def ear(bars: Sequence[Bar], bench: Sequence[Bar], reports: Sequence[EarningsReport]) -> Signal:
    rel = [r for r in reports if r.report_date is not None]
    if not rel or len(bars) < 5:
        return Signal("ear", None, None, "실적 발표 후 추세: 최근 실적 발표 기록 없음")
    last = max(rel, key=lambda r: (r.released_at.isoformat() if r.released_at else r.report_date.isoformat()))
    day, after_close = _reaction_start(last.released_at, last.report_date)
    days = [b.day for b in bars]
    # t0: the last close before the news was public; t1: the close of the day after the first reacting session
    i0 = bisect_right(days, day) - 1 if after_close else bisect_left(days, day) - 1
    if i0 < 0:
        return Signal("ear", None, None, "실적 발표 후 추세: 발표 전 가격 없음")
    i1 = i0 + 2
    if i1 >= len(bars):
        return Signal("ear", None, None, f"실적 발표 후 추세: {last.report_date.isoformat()} 발표 — 반응 이틀이 아직 다 지나지 않음")
    age = len(bars) - 1 - i1
    if age > EAR_MAX_AGE:
        return Signal("ear", None, None, f"실적 발표 후 추세: 마지막 발표({last.report_date.isoformat()})가 {EAR_MAX_AGE}거래일보다 오래됨")
    r = bars[i1].close / bars[i0].close - 1
    bmap = {b.day: b.close for b in bench}
    b0, b1 = bmap.get(bars[i0].day), bmap.get(bars[i1].day)
    if not b0 or not b1:
        return Signal("ear", None, None, "실적 발표 후 추세: 같은 날의 시장(SPY) 가격 없음")
    ex = r - (b1 / b0 - 1)
    word = "좋게 받아들여짐 — 추세가 이어지는 경향" if ex >= 0.02 else "나쁘게 받아들여짐 — 약세가 이어지는 경향" if ex <= -0.02 else "시장 반응 미미"
    return Signal("ear", round(ex, 4), round(lin(ex, -0.08, 0.08), 4),
                  f"{last.report_date.isoformat()} 실적 발표 반응 {ex:+.1%}(시장 대비, {age}거래일 전) — {word}")


# ------------------------------------------------------------------ the component
@dataclass(frozen=True, slots=True)
class ReturnSignals:
    signals: tuple[Signal, ...]

    @property
    def available(self) -> tuple[Signal, ...]:
        return tuple(s for s in self.signals if s.sub is not None)

    @property
    def subscore(self) -> float | None:
        a = self.available
        return round(sum(s.sub for s in a) / len(a), 4) if len(a) >= MIN_SIGNALS else None  # type: ignore[misc]

    @property
    def coverage(self) -> float:
        return round(len(self.available) / len(self.signals), 4) if self.signals else 0.0

    def get(self, key: str) -> Signal | None:
        return next((s for s in self.signals if s.key == key), None)


def return_signals(bars: Sequence[Bar], bench: Sequence[Bar], quarters: Sequence[QuarterlyFinancials], reports: Sequence[EarningsReport],
                   rs_percentile: float | None, rs_universe: int) -> ReturnSignals:
    return ReturnSignals((high52(bars), rs_rank(bars, rs_percentile, rs_universe), fscore(quarters), ear(bars, bench, reports)))


def grade(sub: float | None) -> str | None:
    """A–F for the screen (fixed cut points, not tuned on results)."""
    if sub is None:
        return None
    return "A" if sub >= 0.8 else "B" if sub >= 0.65 else "C" if sub >= 0.45 else "D" if sub >= 0.3 else "F"
