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


@dataclass(frozen=True, slots=True)
class ShareBasis:
    """Which splits a recorded per-share value (a price level, a quantity, a cost, an EPS, an estimate) already reflects.

    ``applied``: the keys of the splits the value's source had applied when it was recorded (an analysis records the
    splits its bars reflected). ``None``: no record — then every split executed on or before ``as_of`` counts as
    applied (a user's broker quantity, a filing, an older snapshot). ``unknown_on_execution_day``: an outside source
    observed ON an execution day may be on either side of the split — its basis is unknown (never guessed)."""

    as_of: date
    applied: frozenset[str] | None = None
    unknown_on_execution_day: bool = False


def share_multiplier(splits: Sequence[SplitEvent], basis: ShareBasis, through: date) -> float | None:
    """THE share-basis conversion (every path uses this one function): the multiplier from ``basis`` to the basis of
    today's split-adjusted bars — the product of the ratios of the splits executed on or before ``through`` that the
    basis does not reflect. Quantities multiply by it; per-share amounts divide by it. None = the basis is unknown."""
    f = 1.0
    for s in splits:
        if s.split_from <= 0 or s.split_to <= 0 or s.execution_date > through:
            continue
        if basis.applied is not None:
            reflected = split_key(s) in basis.applied
        else:
            if basis.unknown_on_execution_day and s.execution_date == basis.as_of:
                return None
            reflected = s.execution_date <= basis.as_of
        if not reflected:
            f *= s.ratio
    return f


def split_factor(splits: Sequence[SplitEvent], after: date, through: date) -> float:
    """Cumulative share multiplier of splits executed in (after, through] — ``share_multiplier`` with a date basis."""
    return share_multiplier(splits, ShareBasis(after), through) or 1.0


def split_key(s: SplitEvent) -> str:
    return f"{s.execution_date.isoformat()}:{s.split_from}:{s.split_to}"


def encoded_split_keys(encoded: Sequence[object] | None) -> frozenset[str] | None:
    """Keys of splits stored in an encoded analysis input (``inputs["splits"]``); None when the record predates it."""
    if encoded is None:
        return None
    out = []
    for e in encoded:
        if isinstance(e, dict) and e.get("execution_date"):
            out.append(f"{e['execution_date']}:{e.get('split_from')}:{e.get('split_to')}")
    return frozenset(out)


def analysis_basis(as_of: date, applied: Sequence[str] | None) -> ShareBasis:
    """The basis of an analysis (or a stored recommendation / paper signal made from one): the splits it recorded, else
    — records from before the field existed — its date."""
    return ShareBasis(as_of, frozenset(applied) if applied is not None else None)


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
