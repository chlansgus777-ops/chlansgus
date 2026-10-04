"""매매 습관 분석 — pure calculations on the owner's executed orders and the observed prices of the holding period.

The question it answers: does "buy → falls 3–5 % → keep holding → back to about break-even → sell" repeat? A trade
that ended at +1 % may have been at −4 % first; the final return alone cannot tell. Nothing here decides why a sale
was made — the screens call a match a *candidate* and say what the prices show and what they cannot show.

Rules of the calculation (each one has a test in tests/unit/test_habits.py):
- Executions, never order requests: an order counts with its FILLED quantity and average fill price (Toss gives one
  aggregate per order: quantity, average price, commission, tax, the LAST fill time; the individual partial fills are
  not in the API). Orders with nothing filled are not trades.
- FIFO per (account, symbol, currency); fractional quantities in Decimal; an order's fees (commission + tax) are split
  over its matched quantity in proportion. A sale with no recorded purchase left is NOT given a price: it becomes
  "기초 매수 기록 필요". Shares held before the fetched period (current holding − fetched buys + fetched sells) are a
  lot of unknown price consumed first, as FIFO requires.
- Observations: only prices known to lie between the purchase and the sale. Daily bars cannot order the day's
  high/low against a fill inside the session: on the purchase day only the close counts (the open and the whole bar
  too when the purchase came before the open), on the sale day only the open (the whole bar when the sale came after
  the close); the bar's high/low on those days is kept apart as an uncertain extreme and never confirms a pattern.
  The purchase time is the order's last fill (all of it bought by then), the sale cut-off is the sale ORDER time
  (none of it sold before then).
- A fill price outside its own day's bar (beyond a tolerance for extended hours), or a split between the purchase
  and the end of the observed window, means the bars are on another basis: that trade's price path is withheld.
- MAE = lowest certain observation / entry − 1 (≤ 0; 0 without a decline); MFE = highest / entry − 1 (≥ 0).
- Break-even exit candidate (defaults, adjustable): a certain observation ≤ −3 % before the sale, and a net exit
  return (after the fees allocated to that quantity) within [−0.5 %, +3 %]. Counted per SELL ORDER (one sale over
  several lots or fills is one), with the evaluable sale orders as the denominator; 2+ distinct orders → repeated.
- Stop review only against a stop that exists; early-exit review only with the full observation window after the sale.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any, Iterable, Sequence
from zoneinfo import ZoneInfo

from marketlens.domain.market import Bar

KST = ZoneInfo("Asia/Seoul")
NY = ZoneInfo("America/New_York")
ZERO = Decimal(0)
EPS = Decimal("1e-9")
BASIS_TOLERANCE = 0.08  # a fill this far outside its day's bar (extended hours) is still the same price basis


@dataclass(frozen=True)
class Rules:
    """The user's adjustable rule set (defaults = the owner's request of 2026-10-04)."""

    dip_pct: float = -3.0  # a certain observation at or below this (% vs entry) before the sale
    exit_min_pct: float = -0.5  # net exit return at least this …
    exit_max_pct: float = 3.0  # … and at most this
    repeat_min: int = 2  # distinct sale orders for "반복"
    early_days: int = 5  # trading days observed after a sale
    early_rise_pct: float = 5.0  # highest price in those days at least this % above the sale price

    @staticmethod
    def from_dict(d: dict[str, Any] | None) -> "Rules":
        base = Rules()
        if not d:
            return base
        r = replace(base, **{k: type(getattr(base, k))(v) for k, v in d.items() if hasattr(base, k) and v is not None})
        if not (r.exit_min_pct <= r.exit_max_pct) or r.dip_pct >= 0 or r.repeat_min < 1 or not (1 <= r.early_days <= 60) or r.early_rise_pct <= 0:
            raise ValueError("규칙 값이 올바르지 않습니다: 하락폭은 음수, 탈출 수익률 하한 ≤ 상한, 반복 횟수 ≥ 1, 관찰 1–60거래일, 상승률 > 0")
        return r


@dataclass(frozen=True)
class Fill:
    """One executed order (its filled part)."""

    order_id: str
    symbol: str
    side: str  # BUY / SELL
    quantity: Decimal
    price: Decimal
    fees: Decimal  # commission + tax of the whole order
    currency: str
    filled_at: datetime | None  # last fill (Toss: execution.filledAt)
    ordered_at: datetime
    account: str = "toss"
    source: str = "toss"  # toss / csv / sample
    commission: Decimal | None = None
    tax: Decimal | None = None
    status: str = ""

    @property
    def done_at(self) -> datetime:
        """All of the order filled by then."""
        return self.filled_at or self.ordered_at

    @property
    def market(self) -> str:
        return "US" if self.currency == "USD" else "KR" if self.currency == "KRW" else "OTHER"


@dataclass
class Lot:
    buy: Fill | None  # None: shares held before the fetched period (price unknown)
    remaining: Decimal
    fee_per_share: Decimal


@dataclass
class Match:
    """One FIFO pairing of a purchase lot and (part of) a sale."""

    id: str
    symbol: str
    currency: str
    account: str
    quantity: Decimal
    sell: Fill
    buy: Fill | None  # None → 기초 매수 기록 필요
    buy_fees: Decimal
    sell_fees: Decimal
    split_index: int  # nth row of this sale order (0 = first)


@dataclass
class OpenLot:
    symbol: str
    currency: str
    quantity: Decimal
    buy: Fill | None


def _q(d: Decimal) -> Decimal:
    return d if abs(d) > EPS else ZERO


def dedupe(fills: Iterable[Fill]) -> list[Fill]:
    """One record per order: the same order read twice (overlapping fetches, CSV + API) keeps the API's record, else
    the one read last; an order id that is empty never merges two different rows."""
    best: dict[str, Fill] = {}
    for f in fills:
        key = f"{f.account}|{f.order_id}" if f.order_id else f"{f.account}|{f.source}|{f.symbol}|{f.side}|{f.ordered_at.isoformat()}|{f.quantity}|{f.price}"
        cur = best.get(key)
        if cur is None or cur.source != "toss" or f.source == "toss":
            best[key] = f
    return sorted(best.values(), key=lambda f: (f.done_at, f.ordered_at, f.order_id))


def match_fifo(fills: Sequence[Fill], opening: dict[tuple[str, str, str], Decimal] | None = None) -> tuple[list[Match], list[OpenLot], list[str]]:
    """FIFO matches, the lots still held and warnings. ``opening``: (account, symbol, currency) → shares held before
    the first fetched record (price unknown), consumed first."""
    matches: list[Match] = []
    warnings: list[str] = []
    books: dict[tuple[str, str, str], list[Lot]] = {}
    for key, q in (opening or {}).items():
        if q > EPS:
            books.setdefault(key, []).append(Lot(None, q, ZERO))
    for f in dedupe(fills):
        if f.quantity <= 0:
            continue
        key = (f.account, f.symbol, f.currency)
        book = books.setdefault(key, [])
        if f.side == "BUY":
            book.append(Lot(f, f.quantity, f.fees / f.quantity))
            continue
        if f.side != "SELL":
            continue
        left = f.quantity
        sell_fee_per_share = f.fees / f.quantity
        n = 0
        while left > EPS and book:
            lot = book[0]
            take = min(left, lot.remaining)
            matches.append(Match(f"{f.order_id or f.ordered_at.isoformat()}~{n}", f.symbol, f.currency, f.account, take, f, lot.buy,
                                 lot.fee_per_share * take, sell_fee_per_share * take, n))
            n += 1
            lot.remaining = _q(lot.remaining - take)
            left = _q(left - take)
            if lot.remaining <= EPS:
                book.pop(0)
        if left > EPS:  # no recorded purchase left: never given a price
            matches.append(Match(f"{f.order_id or f.ordered_at.isoformat()}~{n}", f.symbol, f.currency, f.account, left, f, None, ZERO,
                                 sell_fee_per_share * left, n))
    open_lots = [OpenLot(sym, cur, lot.remaining, lot.buy) for (_a, sym, cur), book in sorted(books.items()) for lot in book if lot.remaining > EPS]
    return matches, open_lots, warnings


# ------------------------------------------------------------------ price observations
@dataclass(frozen=True)
class Session:
    tz: ZoneInfo
    open: time
    close: time


SESSIONS = {"US": Session(NY, time(9, 30), time(16, 0)), "KR": Session(KST, time(9, 0), time(15, 30))}


def _close_time(market: str, d: date) -> time:
    if market == "US":
        from marketlens.domain.market_calendar import regular_close_time

        return regular_close_time(d)
    return SESSIONS[market].close


def _phase(market: str, ts: datetime) -> tuple[date, str]:
    """The exchange-local date of ``ts`` and whether it was before the open, during the session, or after the close."""
    s = SESSIONS[market]
    local = ts.astimezone(s.tz)
    d, t = local.date(), local.time()
    if t < s.open:
        return d, "pre"
    if t < _close_time(market, d):
        return d, "regular"
    return d, "post"


@dataclass
class Path:
    """What the bars say about one holding period."""

    status: str  # OK / NO_PRICES / BASIS / NO_CERTAIN / UNSUPPORTED
    reason: str = ""
    lows: list[tuple[date, float, str]] = field(default_factory=list)  # (day, price, what) certain
    highs: list[tuple[date, float, str]] = field(default_factory=list)
    uncertain_low: tuple[date, float] | None = None  # buy/sale-day bar extremes (order vs the fill unknown)
    uncertain_high: tuple[date, float] | None = None
    first_bar: date | None = None
    last_bar: date | None = None
    missing_days: list[date] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _weekdays(a: date, b: date) -> list[date]:
    out, d = [], a
    while d <= b:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def expected_sessions(market: str, a: date, b: date) -> list[date]:
    if market == "US":
        from marketlens.domain.market_calendar import is_trading_day

        return [d for d in _weekdays(a, b) if is_trading_day(d)]
    return _weekdays(a, b)  # KR: weekdays (the exchange's holidays are not in the app)


def basis_check(fill_price: float, bar: Bar | None) -> bool:
    """A fill within its day's bar (± the tolerance for extended-hours prints) is on the bars' price basis."""
    if bar is None:
        return True
    return bar.low * (1 - BASIS_TOLERANCE) <= fill_price <= bar.high * (1 + BASIS_TOLERANCE)


def holding_path(market: str, entry: float, exit_price: float, bought_at: datetime, sale_ordered_at: datetime,
                 bars: Sequence[Bar] | None, splits: Sequence[date] = ()) -> Path:
    if market not in SESSIONS:
        return Path("UNSUPPORTED", "이 시장의 거래 시간 정보가 없습니다")
    if not bars:
        return Path("NO_PRICES", "보유 기간 일봉 없음" + (" (국내 종목 일봉은 앱 공급원에 없음 — 가격 CSV로 보완 가능)" if market == "KR" else ""))
    db, pb = _phase(market, bought_at)
    ds, ps = _phase(market, sale_ordered_at)
    by_day = {b.day: b for b in bars}
    if any(db < sd <= ds for sd in splits):
        return Path("BASIS", "보유 중 주식분할(병합) 발생 — 체결가와 조정 일봉의 기준이 달라 분석 보류")
    if not basis_check(entry, by_day.get(db)) or not basis_check(exit_price, by_day.get(ds)):
        return Path("BASIS", "체결가가 그날 일봉 범위를 크게 벗어남(분할·병합 등 가격 기준 차이 의심) — 분석 보류")
    p = Path("OK")
    window = [b for b in bars if db <= b.day <= ds]
    if window:
        p.first_bar, p.last_bar = window[0].day, window[-1].day
    p.missing_days = [d for d in expected_sessions(market, db, ds) if d not in by_day]
    ul: list[tuple[date, float]] = []
    uh: list[tuple[date, float]] = []

    def certain(b: Bar, what: str) -> None:
        p.lows.append((b.day, b.low, what))
        p.highs.append((b.day, b.high, what))

    def point(d: date, v: float, what: str) -> None:
        p.lows.append((d, v, what))
        p.highs.append((d, v, what))

    def unsure(b: Bar) -> None:
        ul.append((b.day, b.low))
        uh.append((b.day, b.high))

    for b in window:
        d = b.day
        if d == db and d == ds:
            if pb == "pre" and ps == "post":
                certain(b, "당일 전체 봉(매수가 개장 전, 매도가 마감 후)")
            elif pb == "pre" and ps == "regular":
                point(d, b.open, "당일 시가")
                unsure(b)
            elif pb == "regular" and ps == "post":
                point(d, b.close, "당일 종가")
                unsure(b)
            else:
                unsure(b)
        elif d == db:
            if pb == "pre":
                certain(b, "매수일 전체 봉(개장 전 매수)")
            elif pb == "regular":
                point(d, b.close, "매수일 종가")
                unsure(b)
        elif d == ds:
            if ps == "post":
                certain(b, "매도일 전체 봉(마감 후 매도 주문)")
            elif ps == "regular":
                point(d, b.open, "매도일 시가")
                unsure(b)
        else:
            certain(b, "보유 중 일봉")
    p.uncertain_low = min(ul, key=lambda x: x[1]) if ul else None
    p.uncertain_high = max(uh, key=lambda x: x[1]) if uh else None
    if not p.lows:
        p.status = "NO_CERTAIN"
        p.reason = "매수와 매도 사이에 시점이 확실한 가격 관측치가 없음(같은 날 매매 등, 일봉만 있음)"
    p.notes.append("일봉(고가·저가·시가·종가) 기준 관측치 — 장중 최저·최고의 정확한 시각은 알 수 없음")
    if ul:
        p.notes.append("매수일·매도일의 고가·저가는 체결 전후 움직임을 포함할 수 있어 확정 계산에서 제외(불확실 값으로 따로 표시)")
    return p


def mae_mfe(entry: float, p: Path) -> tuple[float | None, float | None]:
    if p.status != "OK" or not p.lows:
        return None, None
    lo = min(x[1] for x in p.lows)
    hi = max(x[1] for x in p.highs)
    return min(0.0, (lo / entry - 1) * 100), max(0.0, (hi / entry - 1) * 100)


# ------------------------------------------------------------------ per-trade evaluation
@dataclass
class TradeEval:
    match: Match
    entry: float | None
    exit: float
    gross_pnl: float | None
    net_pnl: float | None
    gross_ret: float | None  # %
    net_ret: float | None  # % (fees of both sides on the entry cost)
    holding_days: float | None
    path: Path | None
    mae: float | None
    mfe: float | None
    pattern: str  # CANDIDATE / NOT / UNCONFIRMED / INSUFFICIENT / NEED_BASIS
    pattern_reason: str


def evaluate(m: Match, rules: Rules, bars: Sequence[Bar] | None, splits: Sequence[date] = ()) -> TradeEval:
    s = m.sell
    exit_p = float(s.price)
    if m.buy is None:
        return TradeEval(m, None, exit_p, None, None, None, None, None, None, None, None, "NEED_BASIS",
                         "기초 매수 기록 필요 — 이 수량의 매수 체결이 조회 기간에 없음(임의의 매수가를 쓰지 않음)")
    b = m.buy
    entry = float(b.price)
    q = float(m.quantity)
    fees = float(m.buy_fees + m.sell_fees)
    gross = (exit_p - entry) * q
    net = gross - fees
    cost = entry * q + float(m.buy_fees)
    gross_ret = (exit_p / entry - 1) * 100
    net_ret = net / cost * 100 if cost > 0 else None
    days = (s.done_at - b.done_at).total_seconds() / 86400
    path = holding_path(b.market, entry, exit_p, b.done_at, s.ordered_at, bars, splits)
    mae, mfe = mae_mfe(entry, path)
    pattern, why = classify(rules, path, entry, mae, net_ret)
    return TradeEval(m, entry, exit_p, gross, net, gross_ret, net_ret, days, path, mae, mfe, pattern, why)


def classify(rules: Rules, path: Path, entry: float, mae: float | None, net_ret: float | None) -> tuple[str, str]:
    if net_ret is None:
        return "INSUFFICIENT", "순수익률 계산 불가"
    in_band = rules.exit_min_pct <= net_ret <= rules.exit_max_pct
    if path.status != "OK" or mae is None:
        if not in_band:  # the exit alone already rules it out
            return "NOT", f"청산 순수익률 {net_ret:+.2f}%가 범위({rules.exit_min_pct:+g}%~{rules.exit_max_pct:+g}%) 밖"
        return "INSUFFICIENT", f"판정 불가 — {path.reason}"
    if mae <= rules.dip_pct:
        if in_band:
            return "CANDIDATE", f"매도 전 확인된 최저 {mae:+.2f}% (기준 {rules.dip_pct:+g}% 이하) 후 순수익률 {net_ret:+.2f}%로 청산"
        return "NOT", f"하락({mae:+.2f}%)은 있었으나 청산 순수익률 {net_ret:+.2f}%가 범위({rules.exit_min_pct:+g}%~{rules.exit_max_pct:+g}%) 밖"
    if not in_band:
        return "NOT", f"청산 순수익률 {net_ret:+.2f}%가 범위 밖"
    # not deep enough among the certain observations: an uncertain sale/buy-day low may still have reached it
    if path.uncertain_low is not None and (path.uncertain_low[1] / entry - 1) * 100 <= rules.dip_pct:
        return "UNCONFIRMED", (f"매수일·매도일 저가 {((path.uncertain_low[1] / entry - 1) * 100):+.2f}%가 기준에 닿지만 체결과의 선후를 알 수 없음 — "
                               "확정 후보로 세지 않음")
    if path.missing_days:
        return "INSUFFICIENT", f"판정 불가 — 보유 기간 일봉 {len(path.missing_days)}일 누락(그날 하락했을 수 있음)"
    return "NOT", f"매도 전 최저 {mae:+.2f}%로 기준({rules.dip_pct:+g}%)에 못 미침"


# ------------------------------------------------------------------ sale orders, early exit, stops
@dataclass
class EarlyExit:
    status: str  # CANDIDATE / NOT / INSUFFICIENT
    reason: str
    max_rise: float | None = None  # % vs sale price
    max_fall: float | None = None
    days_observed: int = 0


def early_exit(market: str, sell: Fill, bars: Sequence[Bar] | None, rules: Rules, splits: Sequence[date] = ()) -> EarlyExit:
    if market not in SESSIONS:
        return EarlyExit("INSUFFICIENT", "이 시장의 거래 시간 정보가 없습니다")
    if not bars:
        return EarlyExit("INSUFFICIENT", "매도 후 일봉 없음")
    ds, ps = _phase(market, sell.done_at)
    if any(sd > ds for sd in splits):
        return EarlyExit("INSUFFICIENT", "매도 후 주식분할 발생 — 가격 기준이 달라 판정 보류")
    by_day = {b.day: b for b in bars}
    price = float(sell.price)
    if not basis_check(price, by_day.get(ds)):
        return EarlyExit("INSUFFICIENT", "매도가가 그날 일봉 범위를 벗어남(가격 기준 차이 의심) — 판정 보류")
    obs: list[tuple[float, float]] = []
    sd = by_day.get(ds)
    if sd is not None:
        if ps == "pre":
            obs.append((sd.high, sd.low))
        elif ps == "regular":
            obs.append((sd.close, sd.close))  # after the last fill: only the close is known to be later
    window = sorted((b for b in bars if b.day > ds), key=lambda b: b.day)[: rules.early_days]
    if len(window) < rules.early_days:
        return EarlyExit("INSUFFICIENT", f"관찰 기간 부족 — 매도 후 {len(window)}/{rules.early_days}거래일만 관측", days_observed=len(window))
    obs += [(b.high, b.low) for b in window]
    rise = (max(h for h, _ in obs) / price - 1) * 100
    fall = (min(lo for _, lo in obs) / price - 1) * 100
    if rise >= rules.early_rise_pct:
        return EarlyExit("CANDIDATE", f"매도 후 {rules.early_days}거래일 내 최고 {rise:+.2f}% (기준 {rules.early_rise_pct:+g}% 이상) — 사후 가격 비교일 뿐, 당시 매도가 잘못됐다는 뜻이 아님",
                         rise, fall, len(window))
    return EarlyExit("NOT", f"매도 후 {rules.early_days}거래일 내 최고 {rise:+.2f}%", rise, fall, len(window))


@dataclass(frozen=True)
class StopPlan:
    stop: float
    target: float | None
    recorded_at: datetime
    source: str  # user / app


@dataclass
class StopReview:
    status: str  # NO_STOP / TOUCHED_HELD / EXIT_BELOW / NOT_TOUCHED / INSUFFICIENT
    reason: str
    stop: float | None = None
    target: float | None = None
    pre_recorded: bool | None = None
    source: str | None = None


def stop_review(te: TradeEval, plan: StopPlan | None) -> StopReview:
    if plan is None or te.match.buy is None:
        return StopReview("NO_STOP", "사전 손절가 미설정")
    pre = plan.recorded_at <= te.match.buy.done_at
    label = ("매수 전 기록" if pre else "사후 입력") + (" · 앱 분석 손절가" if plan.source == "app" else "")
    base = {"stop": plan.stop, "target": plan.target, "pre_recorded": pre, "source": plan.source}
    if te.exit < plan.stop:
        return StopReview("EXIT_BELOW", f"손절가 {plan.stop:g} 아래({te.exit:g})에서 청산 — 검토 후보({label}). 가격 접촉만으로 손절 체결 가능 여부는 단정하지 않음", **base)
    p = te.path
    if p is None or p.status != "OK":
        return StopReview("INSUFFICIENT", f"판정 불가 — {p.reason if p else '가격 경로 없음'} ({label})", **base)
    touched = [d for d, v, _w in p.lows if v <= plan.stop]
    if touched:
        return StopReview("TOUCHED_HELD", f"{touched[0].isoformat()} 관측가가 손절가 {plan.stop:g} 이하였으나 계속 보유 — 검토 후보({label}). "
                                          "가격 접촉만으로 실제 손절 체결이 가능했다거나 규칙을 어겼다고 단정하지 않음", **base)
    return StopReview("NOT_TOUCHED", f"확실한 관측치 중 손절가 {plan.stop:g} 접촉 없음 ({label})", **base)


def utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
