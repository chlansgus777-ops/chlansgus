"""이번 달 할 일 (application/routine.py): the momentum book against the account — sell what the book dropped, buy what
it holds and the account does not, 5 % each in whole shares, leave other holdings alone; never an order."""

from datetime import date

from marketlens.application.routine import plan

BOOK = {"state": "READY", "name": "대형주 모멘텀", "version": "M-NF-1.0", "rebalance_day": "2026-09-30", "execute_day": "2026-10-01",
        "next_rebalance": "2026-10-30", "next_execute": "2026-11-02", "sold": ["OLD"],
        "holdings": [{"ticker": "AAA", "rank": 1}, {"ticker": "BBB", "rank": 2}, {"ticker": "CCC", "rank": 30}]}
PX = {"AAA": 100.0, "BBB": 333.0, "CCC": 50.0, "OLD": 20.0, "KO": 60.0}


def test_sell_dropped_buy_missing_keep_held_and_size_5_percent_in_whole_shares():
    r = plan(BOOK, [("CCC", 10), ("OLD", 50), ("KO", 100)], 98_500.0, True, PX.get, date(2026, 9, 30))
    assert r["state"] == "ACT" and r["headline"] == "이번 달 할 일: 1종목 팔기, 2종목 사기"
    assert [s["ticker"] for s in r["sell"]] == ["OLD"] and r["keep"] == ["CCC"] and r["outside"] == ["KO"]
    total = 98_500 + 10 * 50 + 50 * 20 + 100 * 60  # 106,000
    assert r["account"]["total"] == total and r["account"]["slot"] == total * 0.05
    a, b = r["buy"]
    assert (a["ticker"], a["shares"], a["amount"]) == ("AAA", 52, 5200.0)  # floor(5,300 / (100 × 1.0015)): the cost is part of the 5 %
    assert (b["ticker"], b["shares"]) == ("BBB", 15) and b["amount"] <= total * 0.05
    assert "10-01 미국장 시가" in r["when"] and "주문" in r["note"]


def test_after_the_execution_day_it_says_the_price_moved_and_with_nothing_to_do_it_says_so():
    late = plan(BOOK, [], 100_000.0, True, PX.get, date(2026, 10, 6))
    assert "가격 변동" in late["when"] and late["days_to_next"] == 24
    done = plan(BOOK, [("AAA", 1), ("BBB", 1), ("CCC", 1)], 1000.0, True, PX.get, date(2026, 10, 6))
    assert done["state"] == "DONE" and done["headline"] == "이번 달 할 일 없음" and "2026-10-30" in done["when"]


def test_an_unknown_account_is_an_example_and_a_held_or_missing_book_is_not_a_list():
    r = plan(BOOK, [], 100_000.0, False, PX.get, date(2026, 10, 6))
    assert "예시" in r["note"] and not r["account"]["known"]
    held = plan(BOOK | {"state": "HELD", "reasons": ["09-30 순위를 낼 자료가 아직 부족합니다"]}, [], 1.0, True, PX.get, date(2026, 10, 6))
    assert held["state"] == "PREPARING" and "부족" in held["reasons"][0] and "buy" not in held
    assert plan(None, [], 1.0, True, PX.get, date(2026, 10, 6))["state"] == "COMPUTING"
    nopx = plan(BOOK, [("ZZZ", 3)], 1000.0, True, lambda t: None, date(2026, 10, 6))
    assert nopx["account"]["unpriced"] == ["ZZZ"] and nopx["buy"][0]["shares"] is None


def test_an_unpriced_holding_leaves_the_total_unknown_instead_of_shrinking_every_amount():
    """Independent review 2 F04: a held name without a price and $1,000 cash made the account $1,000 and 5 % $50."""
    px = dict(PX)
    r = plan(BOOK, [("BIGHOLD", 1000)], 1000.0, True, px.get, date(2026, 9, 30))
    assert r["account"]["total"] is None and r["account"]["slot"] is None
    assert all(b["shares"] is None and b["amount"] is None for b in r["buy"]) and "BIGHOLD" in r["warning"]



def test_buys_fit_the_cash_and_the_card_says_how_much_is_missing():
    """Owner-supplied check 2026-10-06: $95,000 in a name outside the strategy and $5,000 cash, 20 names to buy — the
    old card told the account to buy $100,000."""
    book = BOOK | {"sold": [], "holdings": [{"ticker": f"T{i:02d}", "rank": i + 1, "sector": f"S{i % 5}"} for i in range(20)]}
    px = {f"T{i:02d}": 100.0 for i in range(20)} | {"OUT": 100.0}
    r = plan(book, [("OUT", 950)], 5000.0, True, px.get, date(2026, 9, 30))
    f = r["funding"]
    assert r["account"]["total"] == 100_000 and f["available"] == 5000 and f["outside_share"] == 0.95
    assert f["planned"] <= 5000 + 1e-9 and abs(f["needed_full"] - 20 * 49 * 100 * 1.0015) < 1e-6
    assert abs(f["shortfall"] - (f["needed_full"] - 5000)) < 1e-6  # about $93,147 missing, shown on the card
    bought = [b for b in r["buy"] if b["shares"]]
    assert sum(b["shares"] for b in bought) * 100 * 1.0015 <= 5000 and bought[0]["shares"] == 49
    assert r["buy"][1]["status"] == "CASH" and "현금 부족" in r["buy"][1]["reason"] and r["deviations"]
    control = plan(book, [], 100_000.0, True, px.get, date(2026, 9, 30))
    assert all(b["shares"] == 49 and b["status"] == "OK" for b in control["buy"]) and not control["deviations"]  # 20 × 5 %


def test_sells_fund_buys_after_cost_and_with_no_cash_the_card_is_blocked_not_done():
    book = BOOK | {"holdings": [{"ticker": "AAA", "rank": 1, "sector": "Tech"}]}
    r = plan(book, [("OLD", 1000)], 0.0, True, {"AAA": 100.0, "OLD": 20.0}.get, date(2026, 9, 30))
    assert abs(r["funding"]["sell_proceeds"] - 1000 * 20 * (1 - 0.0015)) < 1e-9
    assert r["buy"][0]["shares"] == 9 and r["buy"][0]["status"] == "OK"  # 5 % of $20,000 = $1,000 → 9 shares at $100.15 with cost
    blocked = plan(book | {"sold": []}, [("KO", 10)], 0.0, True, {"AAA": 100.0, "KO": 60.0}.get, date(2026, 9, 30))
    assert blocked["state"] == "BLOCKED" and "살 수 없습니다" in blocked["headline"]


def test_a_buy_over_the_sector_limit_is_not_made_and_is_listed():
    book = BOOK | {"sold": [], "holdings": [{"ticker": "NEW", "rank": 1, "sector": "Tech"}]}
    r = plan(book, [("BIG", 290)], 70_000.0, True, {"NEW": 100.0, "BIG": 100.0}.get, date(2026, 9, 30), sectors={"BIG": "Tech"})
    assert r["buy"][0]["status"] == "SECTOR" and r["buy"][0]["shares"] == 0 and r["deviations"][0]["kind"] == "SECTOR"


def test_strategy_and_own_rules_are_shown_side_by_side_when_they_disagree():
    rules = {"CCC": {"action": "STOP", "action_ko": "손절", "detail": "현재가 ≤ 손절가"}, "OLD": {"action": "ADD", "action_ko": "추가 매수", "detail": "-"},
             "KO": {"action": "TAKE1", "action_ko": "1차 익절", "detail": "현재가 ≥ 1차 익절가"}}
    r = plan(BOOK, [("CCC", 10), ("OLD", 50), ("KO", 100)], 98_500.0, True, PX.get, date(2026, 9, 30), rule_actions=rules)
    c = {x["ticker"]: x for x in r["conflicts"]}
    assert set(c) == {"CCC", "OLD"} and "유지" in c["CCC"]["strategy"] and "손절" in c["CCC"]["rule"] and "직접 정하세요" in c["CCC"]["basis"]
    kinds = [x["kind"] for x in r["today"]]
    assert kinds[0] == "RULE" and "CONFLICT" in kinds and len(r["today"]) <= 5  # KO's own take first, then the disagreements
