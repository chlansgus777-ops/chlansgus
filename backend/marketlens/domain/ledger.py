"""Holdings computed from the user's trade records (docs/design/TRANSACTION_LEDGER.md).

One function, :func:`positions`, turns a company's trades (BUY / SELL / DIVIDEND / a user-entered SPLIT) and the
store's splits into the holding on a date. Rules:

- Events are processed by date; on one date the splits come first (a split takes effect before that session opens,
  so a trade on its execution day is on the new basis), then the trades in entry order (their id).
- A split multiplies the quantity and divides the average cost; the cost total and the realized result do not change.
  A store split and a user SPLIT on the same date with the same ratio are the same split and apply once.
- BUY adds quantity and cost (fees included). SELL realizes against the average cost and may not exceed the holding
  at that point. DIVIDEND is summed separately.
- Only trades and splits on or before ``as_of`` count (point in time).

The ledger never guesses: a record set that sells more than it holds raises :class:`LedgerError` instead of being
clamped. :func:`check_new` / :func:`check_delete` are the only ways a change is accepted; a change is refused for the
failures it causes, never for a break that data arriving later (a split recorded afterwards) already caused.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from marketlens.domain.corporate_actions import SplitEvent, split_key

KINDS = ("BUY", "SELL", "DIVIDEND", "SPLIT")
TOL = 1e-9  # relative share tolerance: float noise from split ratios, never a real share (a dust sale is refused)
SAME_SPLIT_DAYS = 30  # a user SPLIT this close to a stored split of the same ratio is that split (ex-date vs credit date)
MAX_RATIO = 1e4  # a split changes the share count at most 10,000-fold either way
MAX_QUANTITY, MAX_PRICE, MAX_AMOUNT = 1e12, 1e9, 1e12


class LedgerError(ValueError):
    """A trade record that cannot be accepted (the message says why, in the user's language)."""


class LedgerNotFound(LedgerError):
    """The record to change does not exist."""


@dataclass(frozen=True, slots=True)
class Trade:
    id: int
    day: date
    kind: str
    quantity: float = 0.0
    price: float = 0.0
    fees: float = 0.0
    amount: float = 0.0
    split_from: float = 0.0
    split_to: float = 0.0


@dataclass(frozen=True, slots=True)
class Position:
    quantity: float
    cost_basis: float  # cost of the shares still held (fees of the buys included)
    realized_pnl: float  # sales minus the average cost of the shares sold, minus the sale fees
    dividends: float
    fees: float
    splits_applied: tuple[str, ...]  # keys (corporate_actions.split_key) of the splits that changed a holding, in date order
    trades: int  # trades counted up to as_of

    @property
    def avg_cost(self) -> float:
        return self.cost_basis / self.quantity if self.quantity > 0 else 0.0

    def unrealized(self, close: float) -> float:
        """Unrealized result at ``close`` — a close on the basis of today's split-adjusted bars."""
        return self.quantity * close - self.cost_basis


def _finite(*xs: float) -> bool:
    return all(isinstance(x, (int, float)) and math.isfinite(x) for x in xs)


def validate(t: Trade, today: date) -> None:
    """Field checks of one record (the ledger-wide checks are in :func:`check_new`)."""
    if t.kind not in KINDS:
        raise LedgerError(f"거래 종류는 {', '.join(KINDS)} 중 하나여야 함 (받은 값: {t.kind})")
    if t.day > today:
        raise LedgerError(f"미래 날짜의 거래는 적을 수 없음 ({t.day.isoformat()} > {today.isoformat()})")
    if not _finite(t.quantity, t.price, t.fees, t.amount, t.split_from, t.split_to):
        raise LedgerError("숫자가 아닌 값이 있음")
    if t.fees < 0 or t.fees > MAX_AMOUNT:
        raise LedgerError("수수료는 0 이상이어야 함")
    if t.kind in ("BUY", "SELL"):
        if not 0 < t.quantity <= MAX_QUANTITY:
            raise LedgerError("수량은 0보다 커야 함")
        if not 0 < t.price <= MAX_PRICE:
            raise LedgerError("가격은 0보다 커야 함")
    elif t.kind == "DIVIDEND":
        if not 0 < t.amount <= MAX_AMOUNT:
            raise LedgerError("배당 금액은 0보다 커야 함")
    else:
        ratio = t.split_to / t.split_from if t.split_from > 0 else math.inf
        if t.split_from <= 0 or t.split_to <= 0 or not math.isfinite(ratio) or not 1 / MAX_RATIO <= ratio <= MAX_RATIO:
            raise LedgerError(f"분할 비율이 올바르지 않음 (분할 전·후 주식 수는 0보다 크고, 비율은 1/{MAX_RATIO:g}~{MAX_RATIO:g}배)")


def _split_events(trades: Iterable[Trade], splits: Sequence[SplitEvent], as_of: date) -> list[SplitEvent]:
    """Stored splits (one per date and ratio) plus the user's SPLIT records that are not a stored split: a user SPLIT
    of the same ratio within SAME_SPLIT_DAYS of a stored one is that split (the store's date is used)."""
    def rk(s: SplitEvent) -> float:
        return round(s.ratio, 9)

    stored: dict[tuple[date, float], SplitEvent] = {}
    for s in splits:
        if s.split_from > 0 and s.split_to > 0:
            stored.setdefault((s.execution_date, rk(s)), s)
    out = list(stored.values())
    manual: dict[tuple[date, float], SplitEvent] = {}
    for t in trades:
        if t.kind != "SPLIT" or t.split_from <= 0 or t.split_to <= 0:
            continue
        m = SplitEvent("", t.day, t.split_from, t.split_to, "user")
        if any(k[1] == rk(m) and abs((k[0] - m.execution_date).days) <= SAME_SPLIT_DAYS for k in stored):
            continue
        manual.setdefault((m.execution_date, rk(m)), m)
    out += list(manual.values())
    return sorted((s for s in out if s.execution_date <= as_of), key=lambda s: s.execution_date)


@dataclass(frozen=True, slots=True)
class _Walk:
    position: Position
    failures: tuple[tuple[int, str], ...]  # (trade id, reason) of each sale above the holding, in date order


def _walk(trades: Sequence[Trade], splits: Sequence[SplitEvent], as_of: date) -> _Walk:
    """The ledger in date order (splits first on a date, then trades in entry order). A sale above the holding is
    recorded as a failure and — only so the walk can go on to find the later ones — sells what there is."""
    events: list[tuple[date, int, int, object]] = [(s.execution_date, 0, i, s) for i, s in enumerate(_split_events(trades, splits, as_of))]
    events += [(t.day, 1, t.id, t) for t in trades if t.kind != "SPLIT" and t.day <= as_of]
    events.sort(key=lambda e: (e[0], e[1], e[2]))
    qty = cost = realized = dividends = fees = 0.0
    applied: list[str] = []
    failures: list[tuple[int, str]] = []
    n = 0
    for day, _order, _i, ev in events:
        if isinstance(ev, SplitEvent):
            if qty > 0:
                qty *= ev.ratio
                applied.append(split_key(ev))
            continue
        t = ev
        assert isinstance(t, Trade)
        n += 1
        if t.kind == "BUY":
            qty += t.quantity
            cost += t.quantity * t.price + t.fees
            fees += t.fees
        elif t.kind == "SELL":
            if qty <= 0 or t.quantity > qty * (1 + TOL):
                failures.append((t.id, f"{day.isoformat()}: 그날 보유 {qty:g}주보다 많이 매도할 수 없음 (매도 {t.quantity:g}주)"))
            avg = cost / qty if qty > 0 else 0.0
            sold = min(t.quantity, qty)
            realized += sold * (t.price - avg) - t.fees
            fees += t.fees
            if qty - sold <= qty * TOL:
                qty, cost = 0.0, 0.0
            else:
                qty -= sold
                cost -= sold * avg
        elif t.kind == "DIVIDEND":
            dividends += t.amount
    return _Walk(Position(qty, cost, realized, dividends, fees, tuple(applied), n), tuple(failures))


def positions(trades: Sequence[Trade], splits: Sequence[SplitEvent], as_of: date) -> Position:
    """The holding on ``as_of`` from the trades and splits known by then. Raises LedgerError on a sale above the
    holding (never clamped)."""
    w = _walk(trades, splits, as_of)
    if w.failures:
        raise LedgerError(w.failures[0][1])
    if not _finite(w.position.quantity, w.position.cost_basis, w.position.realized_pnl):
        raise LedgerError("계산 결과가 숫자가 아님")
    return w.position


def _new_failures(before: Sequence[Trade], after: Sequence[Trade], splits: Sequence[SplitEvent]) -> list[str]:
    """The sales that fail with the change but did not fail without it: a change is blamed only for what it causes
    (records broken by data that arrived later stay editable, and the break is shown on the holding)."""
    end = date.max
    old = {i for i, _ in _walk(before, splits, end).failures}
    return [msg for i, msg in _walk(after, splits, end).failures if i not in old]


def check_new(trades: Sequence[Trade], new: Trade, splits: Sequence[SplitEvent], today: date) -> None:
    """Accept ``new`` only if it is valid and it makes no sale exceed the holding — itself or a later one (a back-dated
    record can break a later sale)."""
    validate(new, today)
    if any(t.id == new.id for t in trades):
        raise LedgerError(f"거래 번호 {new.id}이(가) 이미 있음")
    bad = _new_failures(trades, list(trades) + [new], splits)
    if bad:
        raise LedgerError(bad[0])


def check_delete(trades: Sequence[Trade], trade_id: int, splits: Sequence[SplitEvent], today: date) -> None:
    """Accept deleting ``trade_id`` only if no sale then exceeds the holding that did not before."""
    if not any(t.id == trade_id for t in trades):
        raise LedgerNotFound(f"거래 {trade_id}을(를) 찾을 수 없음")
    bad = _new_failures(trades, [t for t in trades if t.id != trade_id], splits)
    if bad:
        raise LedgerError(f"이 거래를 지우면 뒤의 매도가 보유보다 많아짐 — {bad[0]}")
