"""Paper trading simulator (no real orders, ever).

Entry uses the first bar that opens strictly *after* the recommendation timestamp (its open price plus
slippage and half the spread). Future prices are never used: the simulator only sees bars whose
``day`` is after the recommendation session, and exits are evaluated bar by bar.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Sequence

from marketlens.domain.enums import ExitReason
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import NY, REGULAR_OPEN, to_ny, trading_days_between


@dataclass(frozen=True, slots=True)
class PaperConfig:
    slippage_bps: float = 5.0
    default_half_spread_bps: float = 2.0
    max_holding_days: int = 60
    t1_exit_fraction: float = 0.5
    move_stop_to_breakeven_after_t1: bool = True
    position_notional: float = 10_000.0


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


def first_tradable_day(recommended_at: datetime) -> date:
    """The first session whose *open* is strictly after the recommendation time."""
    local = to_ny(recommended_at)
    d = local.date()
    open_dt = datetime.combine(d, REGULAR_OPEN, tzinfo=NY)
    if local < open_dt:
        return d
    return date.fromordinal(d.toordinal() + 1)


def simulate(
    signal: PaperSignal,
    bars: Sequence[Bar],
    cfg: PaperConfig | None = None,
    exit_events: Sequence[tuple[date, ExitReason]] = (),
    as_of: date | None = None,
) -> PaperTradeResult:
    """Simulate one paper trade. ``exit_events`` are (day, reason) for thesis invalidation/downgrades;
    the exit happens at the open of the first bar on/after that day."""
    cfg = cfg or PaperConfig()
    earliest = first_tradable_day(signal.recommended_at)
    usable = [b for b in sorted(bars, key=lambda b: b.day) if b.day >= earliest and (as_of is None or b.day <= as_of)]
    if not usable:
        return PaperTradeResult(signal, None, (), True, None, None, None, None, ("no tradable bar after recommendation yet",))
    first = usable[0]
    half_spread = (signal.spread_bps / 2) if signal.spread_bps is not None else cfg.default_half_spread_bps
    entry_px = first.open * (1 + (cfg.slippage_bps + half_spread) / 10_000)
    qty = cfg.position_notional / entry_px
    entry = Fill(first.day, round(entry_px, 4), qty)
    if entry_px <= signal.stop:
        return PaperTradeResult(signal, None, (), False, None, None, None, None, ("gap below stop at open: entry skipped",))

    stop = signal.stop
    remaining = qty
    exits: list[Fill] = []
    t1_done = False
    lo_px = entry_px
    hi_px = entry_px
    sell_cost = (cfg.slippage_bps + half_spread) / 10_000
    events = sorted(exit_events)
    last_day = first.day

    for b in usable:
        last_day = b.day
        # scheduled exits (thesis/downgrade) at the open
        due = [e for e in events if e[0] <= b.day and b.day > entry.day]
        if due and remaining > 0:
            px = b.open * (1 - sell_cost)
            exits.append(Fill(b.day, round(px, 4), remaining, due[0][1]))
            remaining = 0
            break
        lo_px = min(lo_px, b.low)
        hi_px = max(hi_px, b.high)
        # stop first (conservative): gap below stop fills at the open
        if b.low <= stop:
            px = min(b.open, stop) * (1 - sell_cost)
            exits.append(Fill(b.day, round(px, 4), remaining, ExitReason.STOP))
            remaining = 0
            break
        if not t1_done and b.high >= signal.target1:
            part = remaining * cfg.t1_exit_fraction
            px = max(b.open, signal.target1) * (1 - sell_cost)
            exits.append(Fill(b.day, round(px, 4), part, ExitReason.TARGET_1))
            remaining -= part
            t1_done = True
            if cfg.move_stop_to_breakeven_after_t1:
                stop = max(stop, entry_px)
        if t1_done and b.high >= signal.target2 and remaining > 0:
            px = max(b.open, signal.target2) * (1 - sell_cost)
            exits.append(Fill(b.day, round(px, 4), remaining, ExitReason.TARGET_2))
            remaining = 0
            break
        if trading_days_between(entry.day, b.day) >= cfg.max_holding_days and remaining > 0:
            px = b.close * (1 - sell_cost)
            exits.append(Fill(b.day, round(px, 4), remaining, ExitReason.TIME_EXIT))
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


def compute_metrics(results: Sequence[PaperTradeResult], benchmark_returns: Sequence[float | None] = ()) -> PaperMetrics:
    closed = [r for r in results if not r.open and r.return_pct is not None]
    rets = [r.return_pct for r in closed if r.return_pct is not None]
    wins = [x for x in rets if x > 0]
    losses = [x for x in rets if x <= 0]
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None
    eq = [1.0]
    for x in sorted(closed, key=lambda r: r.exits[-1].day if r.exits else date.min):
        eq.append(eq[-1] * (1 + (x.return_pct or 0.0) * 0.1))  # 10% of equity per trade
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
        max_drawdown=max_drawdown(eq),
        average_holding_days=sum(hold) / len(hold) if hold else None,
        average_mae=sum(r.mae_pct for r in closed if r.mae_pct is not None) / len(closed) if closed else None,
        average_mfe=sum(r.mfe_pct for r in closed if r.mfe_pct is not None) / len(closed) if closed else None,
        excess_vs_benchmark=excess,
    )
