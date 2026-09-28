"""Forward returns of the backtest rows — evaluation data only, never an input of an analysis.

Execution at the open of the first session after t (the entry day = day 1); horizon h ends at the close of the h-th
trading day of the NYSE calendar. Total return: splits applied on their execution day, cash dividends reinvested on
the ex-date (``store.total_return_path``). A security that stops trading before the horizon ends (delisting) is
liquidated at its last close, and also recomputed with a −30 % and a −55 % delisting loss.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, timedelta

from marketlens.backtest.store import BacktestData, Lineage, total_return_path
from marketlens.domain.market_calendar import add_trading_days, next_trading_day

HORIZONS = (20, 60)
DELISTING = (0.0, 0.30, 0.55)
DELISTED_AFTER_DAYS = 7  # a lineage whose last bar is this many calendar days before the data end stopped trading


@dataclass(frozen=True)
class Outcome:
    entry: date
    end: date
    ret: dict[float, float]  # delisting assumption → total return
    delisted: bool
    last_day: date


def horizon_end(entry: date, h: int) -> date:
    return add_trading_days(entry, h - 1)


def entry_day(t_day: date) -> date:
    return next_trading_day(t_day)


def outcome(ln: Lineage, t_day: date, h: int, data_end: date) -> Outcome | None:
    """None when the horizon has not ended within the data (and the security still trades)."""
    e = entry_day(t_day)
    end = horizon_end(e, h)
    i0 = bisect_left(ln.days, e)
    if i0 >= len(ln.days):
        return None  # no session after t in the data (delisted right at t, or the data ends)
    delisted = (data_end - ln.days[-1]).days > DELISTED_AFTER_DAYS
    if end > data_end and not delisted:
        return None  # the horizon ends after the data (not matured)
    path = total_return_path(ln, ln.days[i0], min(end, data_end))
    if not path:
        return None
    r_last = path[-1][2] - 1.0
    stopped = delisted and ln.days[-1] < end
    rets = {k: ((1 + r_last) * (1 - k) - 1 if stopped else r_last) for k in DELISTING}
    return Outcome(ln.days[i0], end, rets, stopped, path[-1][0])


def spy_return(spy: Lineage, t_day: date, h: int) -> float | None:
    e = entry_day(t_day)
    end = horizon_end(e, h)
    if not spy.days or spy.days[-1] < end:
        return None
    path = total_return_path(spy, e, end)
    return path[-1][2] - 1.0 if path else None


def daily_outliers(data: BacktestData, threshold: float = 0.5) -> list[tuple[str, str, float]]:
    """Close-to-close moves beyond ±threshold that no split explains (reported, never removed)."""
    out = []
    for ln in data.lineages:
        split_on = {s.execution_date: s.ratio for s in ln.splits}
        for i in range(1, len(ln.days)):
            prev, cur = ln.rows[i - 1][3], ln.rows[i][3]
            if prev > 0:
                r = cur * split_on.get(ln.days[i], 1.0) / prev - 1
                if abs(r) > threshold:
                    out.append((ln.key, ln.days[i].isoformat(), round(r, 4)))
    return out


def series_value(series: list[tuple[date, float]], d: date) -> float | None:
    """The last observation on or before ``d``."""
    i = bisect_right(series, (d, float("inf"))) - 1
    return series[i][1] if i >= 0 else None


def fx_return(dexkous: list[tuple[date, float]], start: date, end: date) -> float | None:
    a, b = series_value(dexkous, start - timedelta(days=1)), series_value(dexkous, end)
    return b / a - 1 if a and b else None
