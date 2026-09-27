"""Invariants of the transaction ledger (round 10 feature a, docs/design/TRANSACTION_LEDGER.md): holdings computed from
trades are split-invariant, order-independent, conserve shares, are point-in-time, and follow the company."""

from __future__ import annotations

import itertools
import math
import random
from datetime import date, timedelta

import pytest

from marketlens.domain.corporate_actions import SplitEvent
from marketlens.domain.ledger import LedgerError, Trade, check_delete, check_new, positions

D = date(2026, 3, 2)


def _trades() -> list[Trade]:
    return [
        Trade(1, D, "BUY", quantity=100, price=50.0, fees=5.0),
        Trade(2, D + timedelta(days=10), "BUY", quantity=50, price=60.0, fees=5.0),
        Trade(3, D + timedelta(days=20), "SELL", quantity=80, price=70.0, fees=5.0),
        Trade(4, D + timedelta(days=30), "DIVIDEND", amount=42.0),
        Trade(5, D + timedelta(days=40), "BUY", quantity=10, price=55.0),
    ]


@pytest.mark.parametrize("frm,to", [(1, 2), (1, 10), (20, 1)])
@pytest.mark.parametrize("split_day_offset", [5, 10, 25, 45])
def test_split_invariance(frm, to, split_day_offset):
    """The same economic trades written as the broker shows them — before the split on the old basis, from the
    execution day on the new basis (quantity x r, price / r) — with the split recorded, or all on the new basis without
    it, give the same value, cost, realized and unrealized result. (_trades() is the economic ledger on the old basis.)"""
    r = to / frm
    split_day = D + timedelta(days=split_day_offset)
    split = SplitEvent("T", split_day, frm, to, "polygon")
    as_of = D + timedelta(days=60)

    def conv(t: Trade, f: float) -> Trade:
        return Trade(t.id, t.day, t.kind, quantity=t.quantity * f, price=t.price / f, fees=t.fees, amount=t.amount)

    as_broker = [conv(t, 1.0 if t.day < split_day else r) for t in _trades()]
    a = positions(as_broker, [split], as_of)
    b = positions([conv(t, r) for t in _trades()], [], as_of)
    close = 64.0 / r  # a post-split close
    assert a.quantity == pytest.approx(b.quantity) and a.cost_basis == pytest.approx(b.cost_basis)
    assert a.realized_pnl == pytest.approx(b.realized_pnl) and a.dividends == pytest.approx(b.dividends)
    assert a.quantity * close == pytest.approx(b.quantity * close)
    assert a.unrealized(close) == pytest.approx(b.unrealized(close))


def test_order_independence():
    base = positions(_trades(), [], D + timedelta(days=60))
    for perm in itertools.islice(itertools.permutations(_trades()), 60):
        p = positions(list(perm), [], D + timedelta(days=60))
        assert (p.quantity, p.cost_basis, p.realized_pnl, p.dividends) == pytest.approx((base.quantity, base.cost_basis, base.realized_pnl, base.dividends))


def test_conservation_on_random_ledgers():
    rng = random.Random(7)
    for _ in range(300):
        trades, qty, tid = [], 0.0, 0
        for k in range(rng.randint(1, 15)):
            day = D + timedelta(days=k * 3)
            tid += 1
            if qty >= 0.1 and rng.random() < 0.4:
                q = math.floor(rng.uniform(0.1, qty) * 1e4) / 1e4  # at most the holding (round() could exceed it)
                t = Trade(tid, day, "SELL", quantity=q, price=rng.uniform(5, 200))
            else:
                q = rng.randint(1, 100)
                t = Trade(tid, day, "BUY", quantity=q, price=rng.uniform(5, 200), fees=rng.uniform(0, 5))
            check_new(trades, t, [], day)
            trades.append(t)
            qty = positions(trades, [], day).quantity
        p = positions(trades, [], D + timedelta(days=100))
        bought = sum(t.quantity for t in trades if t.kind == "BUY")
        sold = sum(t.quantity for t in trades if t.kind == "SELL")
        assert p.quantity == pytest.approx(bought - sold, abs=1e-6) and p.quantity >= -1e-9


def test_point_in_time():
    split = SplitEvent("T", D + timedelta(days=15), 1, 2, "polygon")
    at = D + timedelta(days=12)
    p = positions(_trades(), [split], at)
    assert p.quantity == 150 and p.splits_applied == ()  # the later sale and split are not known yet


def test_failure_modes():
    ts = _trades()
    with pytest.raises(LedgerError, match="보유"):
        check_new(ts, Trade(9, D + timedelta(days=21), "SELL", quantity=71, price=10.0), [], D + timedelta(days=60))  # 70 held
    with pytest.raises(LedgerError, match="미래"):
        check_new(ts, Trade(9, D + timedelta(days=90), "BUY", quantity=1, price=10.0), [], D + timedelta(days=60))
    for bad in (Trade(9, D, "BUY", quantity=0, price=10.0), Trade(9, D, "BUY", quantity=1, price=-1.0), Trade(9, D, "BUY", quantity=1, price=1.0, fees=-1.0),
                Trade(9, D, "DIVIDEND", amount=0.0), Trade(9, D, "SPLIT", split_from=0, split_to=2), Trade(9, D, "HOLD", quantity=1, price=1.0)):
        with pytest.raises(LedgerError):
            check_new(ts, bad, [], D + timedelta(days=60))
    with pytest.raises(LedgerError, match="매도"):  # removing the first buy would leave the sale above the holding
        check_delete(ts, 1, [], D + timedelta(days=60))
    check_delete(ts, 5, [], D + timedelta(days=60))  # the last buy can go


def test_a_split_recorded_twice_counts_once():
    split = SplitEvent("T", D + timedelta(days=5), 1, 2, "polygon")
    manual = Trade(10, D + timedelta(days=5), "SPLIT", split_from=1, split_to=2)
    a = positions(_trades()[:1], [split], D + timedelta(days=6))
    b = positions(_trades()[:1] + [manual], [split], D + timedelta(days=6))
    assert a.quantity == b.quantity == 200


def test_a_trade_on_the_execution_day_is_on_the_new_basis():
    split = SplitEvent("T", D + timedelta(days=5), 1, 10, "polygon")
    ts = [Trade(1, D, "BUY", quantity=10, price=100.0), Trade(2, D + timedelta(days=5), "BUY", quantity=10, price=10.0)]
    p = positions(ts, [split], D + timedelta(days=6))
    assert p.quantity == 110 and p.avg_cost == pytest.approx(1100.0 / 110)


def test_boundaries_empty_resell_and_a_split_before_the_first_buy():
    empty = positions([], [], D)
    assert (empty.quantity, empty.cost_basis, empty.realized_pnl, empty.avg_cost, empty.trades) == (0, 0, 0, 0, 0)
    ts = [Trade(1, D, "BUY", quantity=10, price=10.0), Trade(2, D + timedelta(days=1), "SELL", quantity=10, price=12.0),
          Trade(3, D + timedelta(days=2), "BUY", quantity=5, price=20.0)]
    p = positions(ts, [], D + timedelta(days=3))
    assert p.quantity == 5 and p.avg_cost == pytest.approx(20.0) and p.realized_pnl == pytest.approx(20.0)  # the old cost does not leak
    early = SplitEvent("T", D - timedelta(days=30), 1, 4, "polygon")  # before anything was bought: changes nothing
    assert positions(ts, [early], D + timedelta(days=3)).quantity == 5
    same_day = [Trade(1, D, "BUY", quantity=10, price=10.0), Trade(2, D, "SELL", quantity=10, price=11.0)]  # bought and sold the same day
    assert positions(same_day, [], D).quantity == 0
    buy = [Trade(5, D, "BUY", quantity=1, price=1.0)]  # same-day records run in entry order (their id)
    with pytest.raises(LedgerError, match="보유"):
        check_new(buy, Trade(3, D, "SELL", quantity=1, price=1.0), [], D)  # ordered before the buy
    check_new(buy, Trade(6, D, "SELL", quantity=1, price=1.0), [], D)  # entered after it


# ---------------------------------------------------------------- round 10 code review findings (written before the fix)
def test_a_user_split_on_another_day_than_the_stored_one_counts_once():
    """The user writes the split on the day the broker credited the shares; the store records the ex-date days later."""
    buy = Trade(1, D, "BUY", quantity=10, price=100.0)
    manual = Trade(2, D + timedelta(days=5), "SPLIT", split_from=1, split_to=10)
    stored = SplitEvent("T", D + timedelta(days=8), 1, 10, "polygon")
    p = positions([buy, manual], [stored], D + timedelta(days=20))
    assert p.quantity == 100 and len(p.splits_applied) == 1


def test_a_change_is_blamed_only_for_a_failure_it_causes():
    """Records broken by data that arrived later (a reverse split recorded after the sale was entered): an unrelated
    change is accepted (the break is shown on the holding), a change that makes it worse or earlier is refused."""
    ts = [Trade(1, D, "BUY", quantity=10, price=10.0), Trade(2, D + timedelta(days=2), "DIVIDEND", amount=1.0),
          Trade(3, D + timedelta(days=4), "SELL", quantity=10, price=11.0)]
    late = SplitEvent("T", D + timedelta(days=3), 2, 1, "polygon")  # 10 → 5 before the sale of 10
    with pytest.raises(LedgerError):
        positions(ts, [late], D + timedelta(days=10))
    check_delete(ts, 2, [late], D + timedelta(days=10))  # the dividend has nothing to do with it
    check_new(ts, Trade(4, D + timedelta(days=6), "DIVIDEND", amount=1.0), [late], D + timedelta(days=10))
    check_delete(ts, 3, [late], D + timedelta(days=10))  # removing the broken sale fixes it
    with pytest.raises(LedgerError, match="보유"):  # a new sale before the broken one moves the break earlier
        check_new(ts, Trade(5, D + timedelta(days=1), "SELL", quantity=11, price=10.0), [late], D + timedelta(days=10))


def test_absurd_split_ratios_and_dust_sales_are_refused():
    ts = [Trade(1, D, "BUY", quantity=10, price=10.0)]
    for frm, to in ((1e-320, 1e6), (1, 1e9), (1e9, 1)):
        with pytest.raises(LedgerError, match="비율"):
            check_new(ts, Trade(2, D, "SPLIT", split_from=frm, split_to=to), [], D)
    with pytest.raises(LedgerError, match="보유"):
        check_new([], Trade(1, D, "SELL", quantity=5e-7, price=100.0, fees=3.0), [], D)  # nothing held


def test_only_splits_that_changed_the_holding_are_listed():
    old = SplitEvent("T", D - timedelta(days=300), 1, 4, "polygon")
    p = positions([Trade(1, D, "BUY", quantity=1, price=10.0)], [old], D + timedelta(days=1))
    assert p.splits_applied == ()


def test_deleting_a_missing_record_says_not_found():
    from marketlens.domain.ledger import LedgerNotFound

    with pytest.raises(LedgerNotFound):
        check_delete(_trades(), 99, [], D + timedelta(days=60))
