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


def _world(end: date, names: int = 30):
    days = _sessions(end - timedelta(days=500), end)
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
    now = datetime(2026, 11, 7, 12, 0, tzinfo=timezone.utc)
    src = lambda a, b: {t: [x for x in bs if a <= x.day <= b] for t, bs in bars.items()}  # noqa: E731
    mb = MomentumBook(src, lambda: now, tmp_path, lambda: sector, min_names=30)
    r = mb.book()
    mb.book()
    lines = (tmp_path / "momentum_forward.jsonl").read_text().splitlines()
    assert [json.loads(x)["day"] for x in lines] == ["2026-10-30"] and date.fromisoformat("2026-10-30") > CHOSEN_ON
    f = r["forward"]
    assert f["started"] == "2026-10-30" and len(f["open"]) == 15 and 0.7 < f["invested"] <= 0.76  # 15 × 5 %, after costs
    assert "실거래" in f["note"]


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
    # the app's own bar: fewer than 500 names with a year of bars → held as well
    full, sector2, _d = _world(date(2026, 10, 5))
    r2 = MomentumBook(lambda a, b: {t: [x for x in bs if a <= x.day <= b] for t, bs in full.items()}, lambda: now, tmp_path, lambda: sector2).book()
    assert r2["state"] == "HELD" and r2["ready_names"] == 30 and "500개 이상" in r2["reasons"][0]
    assert not (tmp_path / "momentum_forward.jsonl").exists()
