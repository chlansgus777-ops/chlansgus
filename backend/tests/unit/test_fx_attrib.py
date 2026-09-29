"""Won-based return split (domain/fx_attrib.py): the stock effect and the currency effect add up to the won P&L exactly;
the purchase rate comes from the dated buys (average-cost for sales); unknown is said, never guessed."""

from __future__ import annotations

from datetime import date

import pytest

from marketlens.domain.fx_attrib import Lot, attribute, purchase_rate, rate_on, totals

R = {date(2026, 1, 5): 1300.0, date(2026, 3, 2): 1400.0, date(2026, 6, 1): 1350.0}


def test_the_two_effects_add_up_to_the_won_pnl():
    a = attribute("NVDA", 10, 100.0, 108.0, 1339.0, [Lot(date(2026, 1, 5), "BUY", 10, 100.0)], R)
    assert a.known and a.buy_fx == 1300.0
    assert a.stock_krw == round(10 * 8 * 1300) and a.fx_krw == round(10 * 108 * 39)
    assert a.stock_krw + a.fx_krw == a.total_krw == a.value_krw - a.cost_krw
    assert a.stock_pct == pytest.approx(0.08) and a.fx_pct == pytest.approx(0.03) and a.total_pct == pytest.approx(1.08 * 1.03 - 1)


def test_purchase_rate_is_share_weighted_and_a_sale_keeps_the_average():
    lots = [Lot(date(2026, 1, 5), "BUY", 10, 100.0), Lot(date(2026, 3, 2), "BUY", 10, 100.0), Lot(date(2026, 6, 1), "SELL", 5, 120.0)]
    f0, qty, usd, why = purchase_rate(lots, R)
    assert why is None and f0 == pytest.approx(1350.0) and qty == pytest.approx(15) and usd == pytest.approx(1500)


def test_a_holiday_takes_the_last_rate_but_never_a_week_old_gap():
    assert rate_on(R, date(2026, 1, 7)) == 1300.0  # two days after the last rate
    assert rate_on(R, date(2026, 1, 20)) is None  # more than 7 days: unknown, not guessed
    a = attribute("X", 1, 100, 110, 1400, [Lot(date(2026, 1, 20), "BUY", 1, 100)], R)
    assert not a.known and "환율 없음" in a.reason


@pytest.mark.parametrize(("lots", "price", "fx", "words"), [
    (None, 110.0, 1400.0, "직접 입력"),
    ([Lot(date(2026, 1, 5), "BUY", 3, 100.0)], 110.0, 1400.0, "맞지 않음"),  # records cover 3 of 10 shares
    ([Lot(date(2026, 1, 5), "BUY", 10, 100.0)], None, 1400.0, "평가 가격 없음"),
    ([Lot(date(2026, 1, 5), "BUY", 10, 100.0)], 110.0, None, "현재 환율 없음"),
])
def test_unknown_is_said_with_its_reason(lots, price, fx, words):
    a = attribute("X", 10, 100.0, price, fx, lots, R)
    assert not a.known and words in a.reason and a.total_krw is None


def test_totals_are_over_the_known_rows_only():
    k = attribute("A", 10, 100.0, 108.0, 1339.0, [Lot(date(2026, 1, 5), "BUY", 10, 100.0)], R)
    u = attribute("B", 10, 100.0, 108.0, 1339.0, None, R)
    t = totals([k, u])
    assert t["count"] == 1 and t["total_krw"] == k.total_krw and t["stock_pct"] + t["fx_pct"] == pytest.approx(t["total_pct"])
    assert totals([u])["total_krw"] is None
