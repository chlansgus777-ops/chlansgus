"""The owner's 대형주 모멘텀 in the app (application/momentum_book.py): the backtest's month-end ranking and book on
stored bars, a forward record only after the choice, and the paper account from the backtest's own engine."""

import json
from datetime import date, datetime, timedelta, timezone

import numpy as np

from marketlens.application.momentum_book import CHOSEN_ON, MomentumBook, month_end, prepare, rank_at, rebalance
from marketlens.domain import strategies as S
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import is_trading_day


def _sessions(start: date, end: date) -> list[date]:
    out, d = [], start
    while d <= end:
        if is_trading_day(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def _world(end: date, names: int = 30, start: date | None = None):
    days = _sessions(start or end - timedelta(days=500), end)
    rng = np.random.default_rng(5)
    bars = {}
    for j in range(names):
        c = 50 * np.cumprod(1 + 0.0002 * (j + 1) + rng.normal(0, 0.001, len(days)))
        bars[f"S{j:02d}"] = [Bar(d, x, x * 1.01, x * 0.99, x, 2e6) for d, x in zip(days, c)]
    bars["SPY"] = [Bar(d, 400.0, 401.0, 399.0, 400.0, 1e8) for d in days]
    return bars, {f"S{j:02d}": f"sec{j % 3}" for j in range(names)}, days


def test_month_end_follows_the_market_calendar():
    assert month_end(date(2026, 9, 30)) and month_end(date(2026, 10, 30)) and not month_end(date(2026, 10, 29))


def test_the_ranking_is_the_backtests_top_20_and_the_book_keeps_5_per_sector():
    bars, sector, days = _world(date(2026, 9, 30))
    prep = prepare(bars, sector, days[-1])
    top, keep = rank_at(prep, days[-1], min_names=30)  # a 30-name test market (the app needs 500)
    direct = sorted(sector, key=lambda t: -S.momentum(np.array([b.close for b in bars[t]]))[-1])
    assert [t for t, _m in top] == direct[:20] and keep == set(direct[:40])
    held, bought, sold = rebalance([], top, keep, sector)
    assert len(held) == 15 and bought == held and not sold  # three sectors × 5
    assert all(sum(1 for x in held if sector[x] == s) <= 5 for s in set(sector.values()))
    held2, _b, sold2 = rebalance(held + ["GONE"], top, keep, sector)
    assert "GONE" in sold2 and "GONE" not in held2


def test_the_book_shows_holdings_and_records_nothing_before_the_choice(tmp_path):
    bars, sector, _days = _world(date(2026, 10, 5))
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    mb = MomentumBook(lambda a, b: {t: [x for x in bs if a <= x.day <= b] for t, bs in bars.items()}, lambda: now, tmp_path, lambda: sector, min_names=30)
    r = mb.book()
    assert r["state"] == "READY" and r["rebalance_day"] == "2026-09-30" and r["execute_day"] == "2026-10-01"
    assert len(r["holdings"]) == 15 and r["validation"]["status"] == "OWNER" and r["next_rebalance"] == "2026-10-30"
    assert not (tmp_path / "momentum_forward.jsonl").exists() and r["forward"]["started"] is None  # 9/30 is before the choice


def test_after_the_first_month_end_the_ranking_is_recorded_once_and_paper_filled_at_the_next_open(tmp_path):
    bars, sector, _days = _world(date(2026, 11, 6))
    src = lambda a, b: {t: [x for x in bs if a <= x.day <= b] for t, bs in bars.items()}  # noqa: E731
    clock = [datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)]  # seen after the month-end close, before the next open
    mb = MomentumBook(src, lambda: clock[0], tmp_path, lambda: sector, min_names=30)
    mb.book()
    clock[0] = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    r = mb.book()
    lines = [json.loads(x) for x in (tmp_path / "momentum_forward.jsonl").read_text().splitlines()]
    assert [x["day"] for x in lines] == ["2026-10-30"] and lines[0]["timely"] and date.fromisoformat("2026-10-30") > CHOSEN_ON
    f = r["forward"]
    assert f["started"] == "2026-10-30" and len(f["open"]) == 15 and 0.7 < f["invested"] <= 0.76  # 15 × 5 %, after costs
    assert "실거래" in f["note"] and "배당 미포함" in f["note"]


def test_a_month_end_first_seen_after_its_next_open_is_kept_as_late_and_never_filled(tmp_path):
    """Independent review 0444a21 F04 / review 2 F01: opening the app on 11-07 must not buy the 11-02 open."""
    bars, sector, _days = _world(date(2026, 12, 4))
    src = lambda a, b: {t: [x for x in bs if a <= x.day <= b] for t, bs in bars.items()}  # noqa: E731
    r = MomentumBook(src, lambda: datetime(2026, 12, 7, 12, 0, tzinfo=timezone.utc), tmp_path, lambda: sector, min_names=30).book()
    lines = [json.loads(x) for x in (tmp_path / "momentum_forward.jsonl").read_text().splitlines()]
    assert [x["day"] for x in lines] == ["2026-11-30"] and not lines[0]["timely"]  # only the latest month, never 10-30 back-filled
    assert r["forward"]["started"] is None and r["forward"]["open"] == [] and r["forward"]["late_records"] == 1


def test_the_paper_account_keeps_its_first_fills_years_later(tmp_path):
    """Review 2 F02: the account must not lose its early trades once they are older than the ranking window."""
    bars, sector, _days = _world(date(2028, 1, 14), start=date(2025, 6, 2))
    src = lambda a, b: {t: [x for x in bs if a <= x.day <= b] for t, bs in bars.items()}  # noqa: E731
    clock = [datetime(2026, 10, 30, 22, 0, tzinfo=timezone.utc)]
    mb = MomentumBook(src, lambda: clock[0], tmp_path, lambda: sector, min_names=30)
    mb.book()
    clock[0] = datetime(2028, 1, 15, 12, 0, tzinfo=timezone.utc)
    f = mb.book()["forward"]
    assert f["started"] == "2026-10-30" and f["measured_from"] == "2026-10-30" and f["first_rankings"][0] == "2026-10-30"
    assert f["invested"] > 0.7 and f["through"] == "2028-01-14"


def test_a_name_missing_from_todays_securities_list_still_ranks_in_its_month(tmp_path):
    """Review 2 F03: the ranking reads every stored name, not only the current list (no survivorship)."""
    bars, sector, days = _world(date(2026, 10, 5))
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    src = lambda a, b: {t: [x for x in bs if a <= x.day <= b] for t, bs in bars.items()}  # noqa: E731
    full = MomentumBook(src, lambda: now, tmp_path / "a", lambda: sector, min_names=30).book()
    top = full["holdings"][0]["ticker"]
    gone = MomentumBook(src, lambda: now, tmp_path / "b", lambda: {t: s for t, s in sector.items() if t != top}, min_names=30).book()
    assert [h["ticker"] for h in gone["holdings"]][:3] == [h["ticker"] for h in full["holdings"]][:3]


def test_a_market_without_enough_history_at_the_latest_month_end_is_held_never_shown_from_an_older_month(tmp_path):
    """Owner report 2026-10-06: the screen showed the 08-31 ranking with one name. Most names were a few sessions short
    of a year at 09-30, and one name with a longer history was ranked alone at 08-31."""
    bars, sector, days = _world(date(2026, 10, 5))
    cut = date(2026, 9, 30)
    short = [d for d in days if d <= cut][-252:][0]  # 252 bars up to 09-30: one short of a 12-1 return there
    first, *rest = list(sector)
    for t in rest:
        bars[t] = [b for b in bars[t] if b.day >= short]
    bars[first] = [b for b in bars[first] if b.day <= date(2026, 9, 1)]  # the one long history stopped trading in September
    now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)
    src = lambda a, b: {t: [x for x in bs if a <= x.day <= b] for t, bs in bars.items()}  # noqa: E731
    r = MomentumBook(src, lambda: now, tmp_path, lambda: sector, min_names=2).book()
    assert r["state"] == "HELD" and not r["holdings"] and r["latest_month_end"] == "2026-09-30" and r["ready_names"] == 0
    assert "09-30" in r["reasons"][0] and "부족" in r["reasons"][0]
    # the app's own floor: fewer than 40 names with a year of bars → held as well
    full, sector2, _d = _world(date(2026, 10, 5))
    r2 = MomentumBook(lambda a, b: {t: [x for x in bs if a <= x.day <= b] for t, bs in full.items()}, lambda: now, tmp_path, lambda: sector2).book()
    assert r2["state"] == "HELD" and r2["ready_names"] == 30 and "40개 이상" in r2["reasons"][0]  # the floor: the top 40
    assert not (tmp_path / "momentum_forward.jsonl").exists()
