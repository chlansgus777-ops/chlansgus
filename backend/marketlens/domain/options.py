"""Options intelligence (used for event risk / priced-in / sizing, never as a directional signal)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Sequence


@dataclass(frozen=True, slots=True)
class OptionsSnapshot:
    source: str
    as_of: date | None = None  # trading day the chain snapshot refers to (None → freshness unknown → unused)
    atm_iv: float | None = None
    iv_history_1y: tuple[float, ...] = ()
    put_call_volume: float | None = None
    put_call_oi: float | None = None
    skew_25d: float | None = None  # put IV − call IV
    atm_straddle_price: float | None = None
    underlying_price: float | None = None
    days_to_expiry: int | None = None
    call_wall: float | None = None
    put_wall: float | None = None
    major_oi_strikes: tuple[float, ...] = ()


def iv_rank(current: float, history: Sequence[float]) -> float | None:
    if len(history) < 20:
        return None
    lo, hi = min(history), max(history)
    if hi == lo:
        return None
    return (current - lo) / (hi - lo)


def iv_percentile(current: float, history: Sequence[float]) -> float | None:
    if len(history) < 20:
        return None
    return sum(1 for h in history if h < current) / len(history)


def expected_move_from_straddle(straddle: float | None, underlying: float | None) -> float | None:
    """Rule of thumb: ~85% of the ATM straddle ≈ 1σ expected move to expiry."""
    if straddle is None or underlying is None or underlying <= 0:
        return None
    return 0.85 * straddle / underlying


def expected_move_from_iv(iv: float | None, days: int | None) -> float | None:
    if iv is None or days is None or days <= 0:
        return None
    return iv * (days / 365) ** 0.5


@dataclass(frozen=True, slots=True)
class OptionsMetrics:
    atm_iv: float | None
    iv_rank: float | None
    iv_percentile: float | None
    expected_move: float | None
    put_call_volume: float | None
    put_call_oi: float | None
    skew: float | None
    call_wall: float | None
    put_wall: float | None


def compute_options_metrics(o: OptionsSnapshot | None) -> OptionsMetrics | None:
    if o is None:
        return None
    em = expected_move_from_straddle(o.atm_straddle_price, o.underlying_price)
    if em is None:
        em = expected_move_from_iv(o.atm_iv, o.days_to_expiry)
    return OptionsMetrics(
        atm_iv=o.atm_iv,
        iv_rank=iv_rank(o.atm_iv, o.iv_history_1y) if o.atm_iv is not None else None,
        iv_percentile=iv_percentile(o.atm_iv, o.iv_history_1y) if o.atm_iv is not None else None,
        expected_move=em,
        put_call_volume=o.put_call_volume,
        put_call_oi=o.put_call_oi,
        skew=o.skew_25d,
        call_wall=o.call_wall,
        put_wall=o.put_wall,
    )


@dataclass(frozen=True, slots=True)
class OwnershipSnapshot:
    """Short interest / insider / institutional — auxiliary factors only."""

    source: str
    short_interest_pct_float: float | None = None
    short_interest_shares: float | None = None
    short_interest_settlement: date | None = None
    days_to_cover: float | None = None
    short_interest_change: float | None = None
    insider_net_buy_value_90d: float | None = None
    insider_transactions: tuple[tuple[str, str, float, str], ...] = ()  # (date, role, value, BUY/SELL)
    institutional_ownership: float | None = None
    institutional_ownership_change: float | None = None
