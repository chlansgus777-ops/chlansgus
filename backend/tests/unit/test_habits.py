"""매매 진단 calculations on VIRTUAL fills and bars (the owner's request of 2026-10-04, §8 cases 1–8, plus the
diagnosis and the mechanical holding plan). Every fill here is made up; no account data is used."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from marketlens.application.habits import build_report, fills_from_toss, opening_inventory
from marketlens.domain.habit_diagnosis import AddEvent, HoldingState, OrderResult, TradeRules, diagnose, holding_plan
from marketlens.domain.habits import Fill, Rules, StopPlan, early_exit, evaluate, match_fifo, stop_review
from marketlens.domain.market import Bar

NY = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")
D0 = date(2026, 3, 2)  # a Monday; the week is all trading days
NOW = datetime(2026, 4, 30, 12, tzinfo=NY)


def day(i: int) -> date:
    d, n = D0, 0
    while n < i:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return d


def at(i: int, hh: int = 11, mm: int = 0) -> datetime:
    return datetime.combine(day(i), datetime.min.time().replace(hour=hh, minute=mm), tzinfo=NY)


def bar(i: int, low: float, high: float, o: float | None = None, c: float | None = None) -> Bar:
    o = o if o is not None else (low + high) / 2
    c = c if c is not None else (low + high) / 2
    return Bar(day(i), o, high, low, c, 1e6)


def flat(n: int, p: float = 100.0, start: int = 0) -> list[Bar]:
    return [bar(start + i, p * 0.995, p * 1.005, p, p) for i in range(n)]


def fill(oid: str, side: str, q: str, p: str, when: datetime, fee: str = "0", sym: str = "AAA", cur: str = "USD", ordered: datetime | None = None) -> Fill:
    return Fill(oid, sym, side, Decimal(q), Decimal(p), Decimal(fee), cur, when, ordered or when - timedelta(minutes=1))


def path_bars(lows: dict[int, float], n: int = 30, p: float = 100.0) -> list[Bar]:
    """Flat at ``p`` with the given day lows (high stays near p)."""
    out = []
    for i in range(n):
        lo = lows.get(i, p * 0.995)
        out.append(bar(i, lo, max(p * 1.005, lo), p, p))
    return out


def one(fills: list[Fill], bars: list[Bar], rules: Rules = Rules()):  # noqa: ANN201
    ms, _open, _w = match_fifo(fills)
    return [evaluate(m, rules, bars) for m in ms]


# ------------------------------------------------------------------ §8 cases 1–3
def test_1_dip_then_break_even_exit_is_a_candidate():
    te = one([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "101", at(5))], path_bars({3: 96.0}))[0]
    assert te.mae == pytest.approx(-4.0) and te.net_ret == pytest.approx(1.0)
    assert te.pattern == "CANDIDATE"


def test_2_no_dip_is_not_a_candidate():
    te = one([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "102", at(5))], path_bars({}))[0]
    assert te.mae > -1 and te.pattern == "NOT"


def test_3_dip_but_exit_beyond_the_band_is_not_a_candidate():
    bars = [bar(5, 104.0, 111.0, 105.0, 110.0) if b.day == day(5) else b for b in path_bars({3: 96.0})]  # rallied into the sale
    te = one([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "110", at(5))], bars)[0]
    assert te.mae == pytest.approx(-4.0) and te.pattern == "NOT" and "범위" in te.pattern_reason


# ------------------------------------------------------------------ §8 case 4: quantities and fees preserved
def test_4_split_buys_and_sells_keep_quantity_and_fees():
    fs = [fill("b1", "BUY", "10", "100", at(1), "1.0"), fill("b2", "BUY", "5.5", "110", at(2), "0.55"),
          fill("s1", "SELL", "8", "120", at(3), "0.8"), fill("s2", "SELL", "7.5", "115", at(4), "0.75")]
    ms, open_lots, _ = match_fifo(fs)
    assert [(m.sell.order_id, m.buy.order_id, m.quantity) for m in ms] == [("s1", "b1", Decimal("8")), ("s2", "b1", Decimal("2")), ("s2", "b2", Decimal("5.5"))]
    assert sum(m.buy_fees for m in ms) == Decimal("1.55") and sum(m.sell_fees for m in ms) == Decimal("1.55")
    assert sum(m.quantity for m in ms) == Decimal("15.5") and not open_lots
    # fractional shares and a remainder still held
    ms2, open2, _ = match_fifo([fill("b", "BUY", "0.75", "200", at(1), "0.3"), fill("s", "SELL", "0.25", "210", at(2), "0.1")])
    assert ms2[0].quantity == Decimal("0.25") and ms2[0].buy_fees == Decimal("0.1")
    assert open2[0].quantity == Decimal("0.50")


# ------------------------------------------------------------------ §8 case 5: one sale order is counted once
def test_5_one_sale_over_many_lots_and_a_duplicate_read_counts_once():
    fs = [fill("b1", "BUY", "3", "100", at(1)), fill("b2", "BUY", "3", "100", at(1, 12)), fill("b3", "BUY", "4", "100", at(1, 13)),
          fill("s", "SELL", "10", "101", at(5)), fill("s", "SELL", "10", "101", at(5))]  # the same order read twice
    rep = build_report(fs, None, True, lambda *_: path_bars({3: 96.0}), lambda _s: [], Rules(), TradeRules(), now=NOW)
    assert rep["summary"]["matched_rows"] == 3 and rep["summary"]["sell_orders"] == 1
    assert rep["summary"]["breakeven"]["candidates"] == 1 and rep["summary"]["breakeven"]["evaluable"] == 1
    assert rep["orders"][0]["rows"] == 3 and rep["summary"]["breakeven"]["repeated"] is False


def test_5b_two_distinct_sale_orders_make_a_repeat():
    fs = [fill("b1", "BUY", "10", "100", at(1)), fill("s1", "SELL", "10", "101", at(5)),
          fill("b2", "BUY", "10", "100", at(8)), fill("s2", "SELL", "10", "100.5", at(12))]
    rep = build_report(fs, None, True, lambda *_: path_bars({3: 96.0, 10: 96.5}), lambda _s: [], Rules(), TradeRules(), now=NOW)
    assert rep["summary"]["breakeven"]["candidates"] == 2 and rep["summary"]["breakeven"]["repeated"] is True
    assert rep["by_symbol"][0]["repeated"] is True and rep["by_symbol"][0]["ratio"] == 1.0


# ------------------------------------------------------------------ §8 case 6: only the item lacking data is undecided
def test_6_missing_prices_stop_or_window_make_only_that_item_undecided():
    fs = [fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "101", at(5))]
    te = one(fs, [])[0]
    assert te.pattern == "INSUFFICIENT" and te.net_ret == pytest.approx(1.0)  # P&L still there
    assert stop_review(te, None).status == "NO_STOP" and "미설정" in stop_review(te, None).reason
    ee = early_exit("US", fs[1], path_bars({}, n=8), Rules())  # 2 days after the sale only
    assert ee.status == "INSUFFICIENT" and "관찰 기간 부족" in ee.reason
    ok = early_exit("US", fs[1], path_bars({}, n=12), Rules())
    assert ok.status == "NOT" and ok.days_observed == 5
    rep = build_report(fs, None, True, lambda *_: None, lambda _s: [], Rules(), TradeRules(), now=NOW)
    assert rep["summary"]["breakeven"]["evaluable"] == 0 and rep["summary"]["data_short"] == 1
    assert rep["orders"][0]["net_pnl"] == pytest.approx(10.0)


def test_6b_a_sale_beyond_the_band_needs_no_prices_to_be_ruled_out():
    te = one([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "120", at(5))], [])[0]
    assert te.pattern == "NOT"


# ------------------------------------------------------------------ §8 case 7: only prices inside the holding
def test_7_prices_before_the_purchase_and_after_the_sale_never_enter_mae_mfe():
    bars = path_bars({0: 50.0, 9: 50.0})  # crashes the day before the purchase and after the sale
    bars = [Bar(b.day, b.open, 200.0, b.low, b.close, b.volume) if b.day in (day(0), day(9)) else b for b in bars]
    te = one([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "101", at(5))], bars)[0]
    assert te.mae > -1 and te.mfe < 1
    # the purchase day's and the sale day's own lows are order-unknown: kept apart, never a confirmed candidate
    te2 = one([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "101", at(5))], path_bars({5: 95.0}))[0]
    assert te2.pattern == "UNCONFIRMED" and te2.path.uncertain_low[1] == 95.0
    te3 = one([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "101", at(5))], path_bars({1: 95.0}))[0]
    assert te3.pattern == "UNCONFIRMED"


def test_7b_a_purchase_before_the_open_and_a_sale_after_the_close_include_those_days():
    te = one([fill("b", "BUY", "10", "100", at(1, 8)), fill("s", "SELL", "10", "101", at(5, 17))], path_bars({1: 96.0}))[0]
    assert te.pattern == "CANDIDATE"
    te2 = one([fill("b", "BUY", "10", "100", at(1, 8)), fill("s", "SELL", "10", "101", at(5, 17))], path_bars({5: 96.0}))[0]
    assert te2.pattern == "CANDIDATE"


def test_7c_same_day_trade_has_no_certain_observation():
    te = one([fill("b", "BUY", "10", "100", at(1, 10)), fill("s", "SELL", "10", "101", at(1, 14))], path_bars({1: 95.0}))[0]
    assert te.path.status == "NO_CERTAIN" and te.pattern == "INSUFFICIENT"


def test_7d_a_split_or_a_fill_off_the_bars_basis_withholds_the_path():
    fs = [fill("b", "BUY", "10", "400", at(1)), fill("s", "SELL", "10", "404", at(5))]  # raw prices vs 4:1-adjusted bars
    te = one(fs, path_bars({3: 96.0}))[0]
    assert te.path.status == "BASIS" and te.pattern == "INSUFFICIENT"
    ms, _o, _w = match_fifo([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "101", at(5))])
    te2 = evaluate(ms[0], Rules(), path_bars({3: 96.0}), [day(3)])
    assert te2.path.status == "BASIS" and "분할" in te2.path.reason


# ------------------------------------------------------------------ §8 case 8
def test_8_duplicates_opening_holdings_time_zones_and_boundaries():
    # duplicates: the same order twice → one
    ms, _o, _w = match_fifo([fill("b", "BUY", "10", "100", at(1)), fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "101", at(5))])
    assert len(ms) == 1 and ms[0].quantity == Decimal("10")
    # shares held before the fetched period: FIFO sells them first, without a price
    fs = [fill("b", "BUY", "5", "100", at(3)), fill("s", "SELL", "5", "101", at(5))]
    opening, warn = opening_inventory(fs, [{"symbol": "AAA", "currency": "USD", "quantity": "5"}], True)
    assert opening == {("toss", "AAA", "USD"): Decimal("5")} and not warn
    ms, open_lots, _ = match_fifo(fs, opening)
    assert ms[0].buy is None and open_lots[0].buy.order_id == "b"
    te = evaluate(ms[0], Rules(), path_bars({}))
    assert te.pattern == "NEED_BASIS" and te.entry is None and "기초 매수 기록 필요" in te.pattern_reason
    rep = build_report(fs, [{"symbol": "AAA", "currency": "USD", "quantity": "5"}], True, lambda *_: path_bars({}), lambda _s: [], Rules(), TradeRules(), now=NOW)
    assert rep["orders"][0]["pattern"] == "NEED_BASIS" and rep["orders"][0]["net_pnl"] is None
    # KST timestamps (Toss) → New York sessions: 23:40 KST = 09:40 EST, a regular-session purchase
    toss, _ = fills_from_toss([{"order_id": "k", "symbol": "AAA", "side": "BUY", "quantity": "10", "avg_price": "100", "commission": "0.1", "tax": None,
                                "currency": "USD", "ordered_at": "2026-03-03T23:39:00+09:00", "filled_at": "2026-03-03T23:40:00+09:00"}])
    assert toss[0].fees == Decimal("0.1") and toss[0].done_at.astimezone(NY).hour == 9
    te_kst = one([toss[0], fill("s", "SELL", "10", "101", at(5))], path_bars({1: 95.0, 3: 96.0}))[0]
    assert te_kst.mae == pytest.approx(-4.0)  # the purchase day's own low (95) is not certain
    # boundaries: exactly −3 % and exactly +3 % / −0.5 % count
    for exit_p, low in (("103", 97.0), ("99.5", 97.0)):
        te_b = one([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", exit_p, at(5))], path_bars({3: low}))[0]
        assert te_b.pattern == "CANDIDATE", (exit_p, te_b.pattern_reason)
    te_out = one([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "103.01", at(5))], path_bars({3: 97.0}))[0]
    assert te_out.pattern == "NOT"


def test_rules_are_adjustable_and_validated():
    r = Rules.from_dict({"dip_pct": -5, "exit_max_pct": 1})
    te = one([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "101", at(5))], path_bars({3: 96.0}), r)[0]
    assert te.pattern == "NOT"
    with pytest.raises(ValueError):
        Rules.from_dict({"dip_pct": 2})


def test_stop_review_needs_a_stop_and_tells_pre_from_post():
    ms, _o, _w = match_fifo([fill("b", "BUY", "10", "100", at(1)), fill("s", "SELL", "10", "99", at(5))])
    te = evaluate(ms[0], Rules(), path_bars({3: 94.0}))
    pre = stop_review(te, StopPlan(95.0, None, at(0), "rule"))
    assert pre.status == "TOUCHED_HELD" and pre.pre_recorded is True and "단정하지 않음" in pre.reason
    post = stop_review(te, StopPlan(99.5, None, at(20), "user"))
    assert post.status == "EXIT_BELOW" and post.pre_recorded is False and "사후 입력" in post.reason


def test_early_exit_is_a_review_with_its_own_drawdown():
    s = fill("s", "SELL", "10", "100", at(5))
    bars = path_bars({}, n=12)
    bars = [Bar(b.day, b.open, 106.0 if b.day == day(7) else b.high, 97.0 if b.day == day(8) else b.low, b.close, b.volume) for b in bars]
    ee = early_exit("US", s, bars, Rules())
    assert ee.status == "CANDIDATE" and ee.max_rise == pytest.approx(6.0) and ee.max_fall == pytest.approx(-3.0)
    assert "잘못됐다는 뜻이 아님" in ee.reason


# ------------------------------------------------------------------ diagnosis and the mechanical plan
def _o(net_ret: float, days: float = 3, mae: float | None = -1.0, sym: str = "AAA", pattern: str = "NOT", early: str = "NOT") -> OrderResult:
    return OrderResult(f"o{net_ret}{sym}{days}", sym, "USD", net_ret * 10, net_ret, days, mae, max(0.0, net_ret + 2), pattern, early, "NO_STOP", 0.0, net_ret * 10)


def test_diagnosis_names_the_losses_and_suggests_rules_from_the_owners_own_trades():
    orders = [_o(2, 2, -1.5), _o(1.5, 3, -2.0), _o(2.5, 2, -1.0), _o(1.0, 2, -3.0), _o(3.0, 1, -0.5),
              _o(-12, 15, -14, "BBB"), _o(-9, 20, -10, "CCC"), _o(0.5, 9, -4.0, "DDD", "CANDIDATE"), _o(0.8, 8, -4.5, "DDD", "CANDIDATE")]
    adds = [AddEvent("BBB", -8.0, -120.0), AddEvent("AAA", 4.0, 20.0)]
    d = diagnose(orders, adds, [], TradeRules())
    ids = [f["id"] for f in d["findings"]]
    assert {"payoff", "big_losses_USD", "hold_losers", "breakeven", "average_down", "no_stop"} <= set(ids)
    big = next(f for f in d["findings"] if f["id"] == "big_losses_USD")
    assert "BBB" in big["evidence"] and "CCC" in big["evidence"]
    assert d["stats"]["wins"] == 7 and d["stats"]["losses"] == 2
    sug = d["suggested"]
    assert -12 <= sug["rules"]["stop_pct"] <= -4 and sug["rules"]["add_mode"] == "winners_only" and sug["why"]


def test_diagnosis_with_few_or_no_trades_says_so():
    assert diagnose([], [], [], TradeRules())["closed"] == 0
    d = diagnose([_o(-5), _o(-4)], [], [], TradeRules())
    assert all(f["confidence"] == "표본 적음" for f in d["findings"])


def _h(price: float, **kw) -> HoldingState:  # noqa: ANN003
    base = dict(symbol="AAA", currency="USD", quantity=10, avg_price=100.0, price=price, price_at=None, price_source="실시간", first_qty=10,
                opened="2026-03-03T10:00:00-05:00", adds_done=0, sold_since_open=False, high_since_open=max(price, 100.0), app_action="HOLD",
                app_stop=None, app_target=None)
    base.update(kw)
    return HoldingState(**base)


def test_the_mechanical_plan_of_a_holding():
    r = TradeRules()
    assert holding_plan(_h(92.5), r)["action"] == "STOP"
    assert holding_plan(_h(111.0), r)["action"] == "TAKE1"
    t = holding_plan(_h(108.0, sold_since_open=True, high_since_open=120.0), r)
    assert t["action"] == "TRAIL" and t["trail"] == pytest.approx(110.4)
    assert holding_plan(_h(106.0), r)["action"] == "ADD"
    hold = holding_plan(_h(97.0), r)
    assert hold["action"] == "HOLD" and any("물타기" in n for n in hold["notes"])
    assert holding_plan(_h(97.0), TradeRules(add_mode="any"))["action"] == "ADD"
    assert holding_plan(_h(106.0, adds_done=1), r)["action"] == "HOLD"
    assert holding_plan(_h(106.0, app_action="SELL"), r)["action"] == "HOLD"
    app = holding_plan(_h(97.0, app_stop=96.0), TradeRules(use_app_stop=True))
    assert app["stop"] == 96.0 and "앱 분석" in app["stop_source"]
    with pytest.raises(ValueError):
        TradeRules.from_dict({"stop_pct": 5})
