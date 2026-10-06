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
    assert (a["ticker"], a["shares"], a["amount"]) == ("AAA", 53, 5300.0)  # floor(5,300 / 100)
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
