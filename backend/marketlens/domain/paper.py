"""Paper trading simulator (no real orders, ever).

Rules that keep simulated results achievable:
- Entry: the first session whose open is strictly after the recommendation timestamp; fill at that open
  plus slippage and half the spread. If that open is above the plan's max-buy price (gap up) or at/below
  the stop (gap down), the trade is skipped.
- Exits triggered by later recommendations (thesis invalidation / downgrade) keep their full
  timestamp and fill at the first open strictly after the signal time.
- Only bars up to ``as_of`` are visible. Within a bar the stop is checked before targets (conservative).
- MAE/MFE only include price action up to the exit fill, never after it.
- Position size follows the action: BUY = 1.0, BUY SMALL / ADD = 0.5 of the base notional.
- Account level (:func:`simulate_account`): entries need real cash, a repeated BUY for a ticker that is
  already held is skipped, ADD requires an open position, the post-trade single-name weight is capped,
  and equity is marked to market every session (cash + open positions at that session's close).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Sequence

from marketlens.domain.enums import ExitReason
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import NY, REGULAR_OPEN, to_ny, trading_days_between

SIZE_FRACTION = {"BUY": 1.0, "BUY SMALL": 0.5, "ADD": 0.5}


@dataclass(frozen=True, slots=True)
class PaperConfig:
    slippage_bps: float = 5.0
    default_half_spread_bps: float = 2.0
    max_holding_days: int = 60
    t1_exit_fraction: float = 0.5
    move_stop_to_breakeven_after_t1: bool = True
    position_notional: float = 10_000.0
    starting_capital: float = 100_000.0
    max_position_weight: float = 0.10  # post-trade weight of one ticker in account equity
    max_open_positions: int = 25


@dataclass(frozen=True, slots=True)
class PaperSignal:
    ticker: str
    recommended_at: datetime
    action: str
    score: float
    confidence: float
    stop: float
    target1: float
    target2: float
    thesis: str
    model_version: str
    regime: str
    sector: str
    spread_bps: float | None = None
    max_buy: float | None = None


@dataclass(frozen=True, slots=True)
class Fill:
    day: date
    price: float
    quantity: float
    reason: ExitReason | None = None


@dataclass(frozen=True, slots=True)
class PaperTradeResult:
    signal: PaperSignal
    entry: Fill | None
    exits: tuple[Fill, ...]
    open: bool
    return_pct: float | None
    mae_pct: float | None
    mfe_pct: float | None
    holding_days: int | None
    notes: tuple[str, ...] = field(default_factory=tuple)

    @property
    def exit_reasons(self) -> tuple[ExitReason, ...]:
        return tuple(e.reason for e in self.exits if e.reason is not None)


def first_tradable_day(ts: datetime) -> date:
    """The first session whose *open* is strictly after ``ts`` (weekends/holidays skipped by bar data)."""
    local = to_ny(ts)
    d = local.date()
    open_dt = datetime.combine(d, REGULAR_OPEN, tzinfo=NY)
    if local < open_dt:
        return d
    return date.fromordinal(d.toordinal() + 1)


def position_notional(action: str, cfg: PaperConfig) -> float:
    return cfg.position_notional * SIZE_FRACTION.get(action, 0.5)


def simulate(
    signal: PaperSignal,
    bars: Sequence[Bar],
    cfg: PaperConfig | None = None,
    exit_events: Sequence[tuple[datetime, ExitReason]] = (),
    as_of: date | None = None,
    notional: float | None = None,
) -> PaperTradeResult:
    """Simulate one paper trade. ``exit_events`` are (signal timestamp, reason); the exit fills at the
    open of the first bar that opens strictly after the signal timestamp."""
    cfg = cfg or PaperConfig()
    earliest = first_tradable_day(signal.recommended_at)
    usable = [b for b in sorted(bars, key=lambda b: b.day) if b.day >= earliest and (as_of is None or b.day <= as_of)]
    if not usable:
        return PaperTradeResult(signal, None, (), True, None, None, None, None, ("추천 이후 거래 가능한 봉이 아직 없음",))
    first = usable[0]
    half_spread = (signal.spread_bps / 2) if signal.spread_bps is not None else cfg.default_half_spread_bps
    entry_px = first.open * (1 + (cfg.slippage_bps + half_spread) / 10_000)
    if entry_px <= signal.stop:
        return PaperTradeResult(signal, None, (), False, None, None, None, None, ("시가가 손절가 아래로 갭하락: 진입 취소",))
    if signal.max_buy is not None and entry_px > signal.max_buy:
        return PaperTradeResult(signal, None, (), False, None, None, None, None, ("시가가 최대 매수가 위로 갭상승: 진입 취소",))
    qty = (notional if notional is not None else position_notional(signal.action, cfg)) / entry_px
    entry = Fill(first.day, round(entry_px, 4), qty)

    exit_days = sorted((first_tradable_day(ts), reason) for ts, reason in exit_events)
    stop = signal.stop
    remaining = qty
    exits: list[Fill] = []
    t1_done = False
    lo_px = entry_px
    hi_px = entry_px
    sell_cost = (cfg.slippage_bps + half_spread) / 10_000
    last_day = first.day
    notes: list[str] = ["모의투자 손절은 장중 손절 주문(가격 도달 시 체결, 갭 하락은 시가 체결)을 가정합니다 — 추천 판단의 '종가 기준 이탈'과 다름"]

    for b in usable:
        last_day = b.day
        due = [e for e in exit_days if e[0] <= b.day]
        if due and remaining > 0:  # exit_days already point to the first open strictly after the signal
            px = b.open * (1 - sell_cost)
            lo_px, hi_px = min(lo_px, b.open), max(hi_px, b.open)
            exits.append(Fill(b.day, round(px, 4), remaining, due[0][1]))
            remaining = 0
            break
        # stop first (conservative): a gap below the stop fills at the open
        if b.low <= stop and not t1_done and b.high >= signal.target1:
            notes.append(f"{b.day.isoformat()}: 같은 날 1차 목표가와 손절가가 모두 도달 — 일봉으로는 순서를 알 수 없어 손절 우선(보수적) 처리")
        if b.low <= stop:
            fill = min(b.open, stop)
            lo_px = min(lo_px, fill)
            hi_px = max(hi_px, b.open)
            exits.append(Fill(b.day, round(fill * (1 - sell_cost), 4), remaining, ExitReason.STOP))
            remaining = 0
            break
        if not t1_done and b.high >= signal.target1:
            part = remaining * cfg.t1_exit_fraction
            fill = max(b.open, signal.target1)
            exits.append(Fill(b.day, round(fill * (1 - sell_cost), 4), part, ExitReason.TARGET_1))
            remaining -= part
            t1_done = True
            if cfg.move_stop_to_breakeven_after_t1:
                stop = max(stop, entry_px)
                if b.low <= stop and remaining > 0:  # the same bar also traded at the new (breakeven) stop
                    exits.append(Fill(b.day, round(stop * (1 - sell_cost), 4), remaining, ExitReason.STOP))
                    notes.append(f"{b.day.isoformat()}: 1차 목표 도달 후 같은 날 본전 손절선도 도달 — 순서 불명, 나머지 물량 본전 청산(보수적)")
                    lo_px, hi_px = min(lo_px, b.low), max(hi_px, b.high)
                    remaining = 0
                    break
        if t1_done and b.high >= signal.target2 and remaining > 0:
            fill = max(b.open, signal.target2)
            lo_px, hi_px = min(lo_px, b.low), max(hi_px, fill)
            exits.append(Fill(b.day, round(fill * (1 - sell_cost), 4), remaining, ExitReason.TARGET_2))
            remaining = 0
            break
        lo_px = min(lo_px, b.low)
        hi_px = max(hi_px, b.high)
        if trading_days_between(entry.day, b.day) >= cfg.max_holding_days and remaining > 0:
            exits.append(Fill(b.day, round(b.close * (1 - sell_cost), 4), remaining, ExitReason.TIME_EXIT))
            remaining = 0
            break

    is_open = remaining > 1e-9
    mark = usable[-1].close if is_open else None
    proceeds = sum(e.price * e.quantity for e in exits) + (remaining * mark if mark is not None else 0.0)
    ret = proceeds / (entry_px * qty) - 1
    return PaperTradeResult(
        signal=signal,
        entry=entry,
        exits=tuple(exits),
        open=is_open,
        return_pct=round(ret, 6),
        mae_pct=round(lo_px / entry_px - 1, 6),
        mfe_pct=round(hi_px / entry_px - 1, 6),
        holding_days=trading_days_between(entry.day, last_day),
        notes=tuple(notes),
    )


@dataclass(frozen=True, slots=True)
class PaperMetrics:
    trades: int
    closed: int
    win_rate: float | None
    average_return: float | None
    median_return: float | None
    profit_factor: float | None
    expectancy: float | None
    max_drawdown: float | None
    average_holding_days: float | None
    average_mae: float | None
    average_mfe: float | None
    excess_vs_benchmark: float | None


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    m = len(s) // 2
    return s[m] if len(s) % 2 else (s[m - 1] + s[m]) / 2


def max_drawdown(equity: Sequence[float]) -> float | None:
    if len(equity) < 2:
        return None
    peak = equity[0]
    mdd = 0.0
    for v in equity:
        peak = max(peak, v)
        if peak > 0:
            mdd = min(mdd, v / peak - 1)
    return mdd


def equity_curve(results: Sequence[PaperTradeResult], closes: dict[str, dict[date, float]], days: Sequence[date], starting_capital: float) -> list[float]:
    """Daily mark-to-market account equity with all concurrent positions (cash + open positions)."""
    curve: list[float] = []
    for d in days:
        pnl = 0.0
        for r in results:
            if r.entry is None or r.entry.day > d:
                continue
            q_open = r.entry.quantity
            realised = 0.0
            for e in r.exits:
                if e.day <= d:
                    realised += e.quantity * (e.price - r.entry.price)
                    q_open -= e.quantity
            px = closes.get(r.signal.ticker, {}).get(d)
            unreal = q_open * (px - r.entry.price) if px is not None and q_open > 1e-9 else 0.0
            pnl += realised + unreal
        curve.append(starting_capital + pnl)
    return curve


def compute_metrics(results: Sequence[PaperTradeResult], benchmark_returns: Sequence[float | None] = (), equity: Sequence[float] | None = None) -> PaperMetrics:
    """Trade statistics. ``max_drawdown`` comes only from a real mark-to-market account equity curve;
    without one it is None (a curve built from trade returns would hide concurrent open losses)."""
    closed = [r for r in results if not r.open and r.return_pct is not None]
    rets = [r.return_pct for r in closed if r.return_pct is not None]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None
    bench = [b for b in benchmark_returns if b is not None]
    excess = (sum(rets) / len(rets) - sum(bench) / len(bench)) if rets and bench else None
    hold = [r.holding_days for r in closed if r.holding_days is not None]
    return PaperMetrics(
        trades=len(results),
        closed=len(closed),
        win_rate=len(wins) / len(rets) if rets else None,
        average_return=sum(rets) / len(rets) if rets else None,
        median_return=_median(rets),
        profit_factor=pf,
        expectancy=(sum(rets) / len(rets)) if rets else None,
        max_drawdown=max_drawdown(equity) if equity is not None else None,
        average_holding_days=sum(hold) / len(hold) if hold else None,
        average_mae=sum(r.mae_pct for r in closed if r.mae_pct is not None) / len(closed) if closed else None,
        average_mfe=sum(r.mfe_pct for r in closed if r.mfe_pct is not None) / len(closed) if closed else None,
        excess_vs_benchmark=excess,
    )


# ------------------------------------------------------------------------------------------ account


@dataclass(frozen=True, slots=True)
class AccountItem:
    key: str  # e.g. recommendation id
    signal: PaperSignal
    exit_events: tuple[tuple[datetime, ExitReason], ...] = ()


@dataclass(frozen=True, slots=True)
class AccountResult:
    trades: tuple[tuple[str, PaperTradeResult], ...]  # accepted trades (key, result)
    skipped: tuple[tuple[str, str], ...]  # (key, Korean reason)
    pending: tuple[str, ...]  # no tradable session after the recommendation yet
    equity: tuple[tuple[date, float], ...]  # daily mark-to-market equity
    cash: float
    realized_pnl: float
    unrealized_pnl: float
    max_drawdown: float | None
    stale_marks: tuple[str, ...]  # tickers marked at an older close because a bar was missing


def simulate_account(items: Sequence[AccountItem], bars_by_ticker: dict[str, Sequence[Bar]], cfg: PaperConfig | None = None, as_of: date | None = None) -> AccountResult:
    """Event-driven paper account. Per session: entries at the open (with cash freed only by exits on
    earlier sessions — conservative), then that session's exits, then mark-to-market at the close."""
    cfg = cfg or PaperConfig()
    ordered = sorted(items, key=lambda i: (i.signal.recommended_at, i.key))
    sims: dict[str, PaperTradeResult] = {}
    skipped: list[tuple[str, str]] = []
    pending: list[str] = []
    for it in ordered:
        bars = bars_by_ticker.get(it.signal.ticker, ())
        r = simulate(it.signal, bars, cfg, it.exit_events, as_of)
        if r.entry is None and r.open:
            pending.append(it.key)
        elif r.entry is None:
            skipped.append((it.key, r.notes[0] if r.notes else "진입 불가"))
        else:
            sims[it.key] = r
    closes: dict[str, dict[date, float]] = {t: {b.day: b.close for b in bs if as_of is None or b.day <= as_of} for t, bs in bars_by_ticker.items()}
    days = sorted({d for c in closes.values() for d in c} | {r.entry.day for r in sims.values() if r.entry})
    if as_of is not None:
        days = [d for d in days if d <= as_of]
    by_entry: dict[date, list[str]] = {}
    for it in ordered:
        r = sims.get(it.key)
        if r is not None and r.entry is not None:
            by_entry.setdefault(r.entry.day, []).append(it.key)
    signal_of = {it.key: it.signal for it in ordered}

    cash = cfg.starting_capital
    accepted: list[str] = []
    realized = 0.0
    last_close: dict[str, float] = {}
    curve: list[tuple[date, float]] = []
    stale: set[str] = set()

    def open_qty(key: str, d: date, inclusive: bool) -> float:
        r = sims[key]
        q = r.entry.quantity if r.entry else 0.0
        for e in r.exits:
            if e.day < d or (inclusive and e.day == d):
                q -= e.quantity
        return max(0.0, q)

    def equity_at(d: date) -> float:
        val = cash
        for k in accepted:
            q = open_qty(k, d, inclusive=False)
            t = signal_of[k].ticker
            px = last_close.get(t, sims[k].entry.price if sims[k].entry else 0.0)
            val += q * px
        return val

    for d in days:
        # 1) entries at today's open
        for key in by_entry.get(d, []):
            sig = signal_of[key]
            r = sims[key]
            assert r.entry is not None
            held = [k for k in accepted if signal_of[k].ticker == sig.ticker and open_qty(k, d, inclusive=False) > 1e-9]
            if sig.action == "ADD" and not held:
                skipped.append((key, "추가매수(ADD)지만 보유 중인 모의 포지션이 없음"))
                continue
            if sig.action in ("BUY", "BUY SMALL") and held:
                skipped.append((key, "이미 보유 중인 종목의 반복 매수 신호 → 중복 진입 생략"))
                continue
            open_positions = {signal_of[k].ticker for k in accepted if open_qty(k, d, inclusive=False) > 1e-9}
            if sig.ticker not in open_positions and len(open_positions) >= cfg.max_open_positions:
                skipped.append((key, f"동시 보유 종목 수 한도({cfg.max_open_positions}) 초과"))
                continue
            cost = r.entry.price * r.entry.quantity
            if cost > cash + 1e-6:
                skipped.append((key, f"현금 부족(필요 ${cost:,.0f}, 보유 ${cash:,.0f})"))
                continue
            eq = equity_at(d)
            existing = sum(open_qty(k, d, inclusive=False) * last_close.get(sig.ticker, sims[k].entry.price) for k in held)  # type: ignore[union-attr]
            if eq > 0 and (existing + cost) / eq > cfg.max_position_weight + 1e-9:
                skipped.append((key, f"편입 후 종목 비중 {(existing + cost) / eq:.1%} > 한도 {cfg.max_position_weight:.0%}"))
                continue
            cash -= cost
            accepted.append(key)
        # 2) exits during the session
        for k in accepted:
            r = sims[k]
            for e in r.exits:
                if e.day == d and r.entry is not None:
                    cash += e.price * e.quantity
                    realized += (e.price - r.entry.price) * e.quantity
        # 3) mark to market at the close
        for t, c in closes.items():
            if d in c:
                last_close[t] = c[d]
        for k in accepted:
            t = signal_of[k].ticker
            if open_qty(k, d, inclusive=True) > 1e-9 and d not in closes.get(t, {}):
                stale.add(t)
        val = cash
        for k in accepted:
            q = open_qty(k, d, inclusive=True)
            if q > 1e-9:
                val += q * last_close.get(signal_of[k].ticker, sims[k].entry.price)  # type: ignore[union-attr]
        curve.append((d, round(val, 2)))

    unreal = 0.0
    end = days[-1] if days else None
    for k in accepted:
        r = sims[k]
        if end is None or r.entry is None:
            continue
        q = open_qty(k, end, inclusive=True)
        if q > 1e-9:
            unreal += q * (last_close.get(signal_of[k].ticker, r.entry.price) - r.entry.price)
    return AccountResult(
        trades=tuple((k, sims[k]) for k in accepted),
        skipped=tuple(skipped),
        pending=tuple(pending),
        equity=tuple(curve),
        cash=round(cash, 2),
        realized_pnl=round(realized, 2),
        unrealized_pnl=round(unreal, 2),
        max_drawdown=max_drawdown([v for _, v in curve]),
        stale_marks=tuple(sorted(stale)),
    )
