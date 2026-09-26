"""Stock splits: keep prices and per-share fundamentals on ONE share basis.

Split-adjusted price bars (Polygon ``adjusted=true``) are expressed in the share count after every split
the vendor knew about when the bars were retrieved. SEC per-share values (EPS, share counts) are in the
basis of the filing that reported them: a value filed *before* a split is pre-split, a comparative filed
*after* it is already adjusted. So a per-share value is scaled by every split executed after its filing
date and on/before the price basis date. P/E, market cap and EPS growth are then basis-consistent.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from typing import Sequence

from marketlens.domain.fundamentals import QuarterlyFinancials


@dataclass(frozen=True, slots=True)
class SplitEvent:
    ticker: str
    execution_date: date
    split_from: float
    split_to: float
    source: str

    @property
    def ratio(self) -> float:
        """Shares after / shares before (10-for-1 → 10; 1-for-20 reverse split → 0.05)."""
        return self.split_to / self.split_from


def split_factor(splits: Sequence[SplitEvent], after: date, through: date) -> float:
    """Cumulative share multiplier of splits executed in (after, through]."""
    f = 1.0
    for s in splits:
        if after < s.execution_date <= through and s.split_from > 0 and s.split_to > 0:
            f *= s.ratio
    return f


PER_SHARE_DIVIDE = ("eps_diluted",)  # per-share amounts: divide by the share multiplier
SHARE_COUNTS = ("shares_diluted", "shares_outstanding")  # share counts: multiply


def normalize_quarters(quarters: Sequence[QuarterlyFinancials], splits: Sequence[SplitEvent], basis_date: date) -> tuple[list[QuarterlyFinancials], list[str]]:
    """Scale per-share fields to the share basis at ``basis_date``. Returns (quarters, notes)."""
    if not splits:
        return list(quarters), []
    out: list[QuarterlyFinancials] = []
    notes: set[str] = set()
    for q in quarters:
        upd: dict[str, float] = {}
        for k in PER_SHARE_DIVIDE + SHARE_COUNTS:
            v = getattr(q, k)
            if v is None:
                continue
            filed = q.field_filed.get(k, q.filed_date)
            f = split_factor(splits, filed, basis_date)
            if f != 1.0:
                upd[k] = v / f if k in PER_SHARE_DIVIDE else v * f
                notes.add(f"{q.period_end.isoformat()} {k}: 주식분할 반영 ×{f:g}" if k in SHARE_COUNTS else f"{q.period_end.isoformat()} {k}: 주식분할 반영 ÷{f:g}")
        out.append(replace(q, **upd) if upd else q)
    return out, sorted(notes)


def adjust_shares(value: float, as_of: date, splits: Sequence[SplitEvent], basis_date: date) -> float:
    """A share count reported on ``as_of`` expressed in the basis at ``basis_date``."""
    return value * split_factor(splits, as_of, basis_date)
