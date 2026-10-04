"""토스증권 daily candles as the first source of the daily history of the names that matter (application/toss_bars.py):
paged on the spec, kept only where they agree with the stored Polygon sessions, completed sessions only, once per
session, bounded, and Polygon stays the source whenever Toss is not connected, fails or disagrees."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import httpx
import pytest

from marketlens.application import toss_bars as tb
from marketlens.application.data_access import DataAccess
from marketlens.application.market_store import MarketStore
from marketlens.application.toss_bars import TossBars, complete_session, disagreement
from marketlens.domain.market import Bar
from marketlens.domain.market_calendar import is_trading_day, previous_trading_day
from marketlens.infrastructure.db.models import Base
from marketlens.infrastructure.db.session import make_engine, make_session_factory
from marketlens.providers.live.toss import TossClient
from tests.toss_fake import FakeToss, candle

UTC = timezone.utc
FRI = date(2026, 10, 2)
SAT_MORNING_KST = datetime(2026, 10, 3, 1, 0, tzinfo=UTC)  # Friday 21:00 New York: Friday's after-hours have ended


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def sleep(self, s: float) -> None:
        self.t += s


def sessions(n: int, last: date = FRI) -> list[date]:
    out, d = [], last
    while len(out) < n:
        if is_trading_day(d):
            out.append(d)
        d -= timedelta(days=1)
    return out[::-1]


def series(days: list[date], close=lambda i: 100.0 + i, volume=lambda i: 1_000_000.0) -> list[Bar]:
    return [Bar(d, close(i) * 0.99, close(i) * 1.01, close(i) * 0.98, close(i), volume(i)) for i, d in enumerate(days)]


def to_candles(bars: list[Bar]) -> list[dict]:
    return [candle(b.day.isoformat(), b.close, b.volume) for b in reversed(bars)]  # newest first, as the API returns


@pytest.fixture()
def fake() -> FakeToss:
    return FakeToss()


@pytest.fixture()
def clock() -> Clock:
    return Clock()


@pytest.fixture()
def store(tmp_path) -> MarketStore:
    eng = make_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    Base.metadata.create_all(eng)
    return MarketStore(make_session_factory(eng), "LIVE")


def client(fake: FakeToss, clock: Clock) -> TossClient:
    return TossClient(fake.client_id, fake.secret, transport=fake.transport(), clock=clock, sleep=clock.sleep)


def broker(fake: FakeToss, clock: Clock, enabled: bool = True) -> SimpleNamespace:
    c = client(fake, clock)
    return SimpleNamespace(enabled=enabled, configured=c.configured, client=c)


def bars_of(fake: FakeToss, clock: Clock, store: MarketStore, now: datetime = SAT_MORNING_KST, names=None) -> TossBars:
    return TossBars(broker(fake, clock), store, lambda: now, names=names, clock=clock)


def candle_requests(fake: FakeToss) -> list[httpx.Request]:
    return [r for r in fake.requests if r.url.path == "/api/v1/candles"]


# ------------------------------------------------------------------ the client
def test_daily_candles_page_back_on_the_spec_and_come_oldest_first(fake, clock):
    days = sessions(320)
    fake.candles["NVDA"] = to_candles(series(days))
    got = client(fake, clock).daily_candles("NVDA", days[0] + timedelta(days=30), max_pages=3)
    assert fake.errors == []  # every request and response follows the pinned spec (count ≤ 200, interval enum, …)
    reqs = candle_requests(fake)
    assert len(reqs) == 2 and all(r.url.params["interval"] == "1d" and r.url.params["adjusted"] == "true" for r in reqs)
    assert reqs[1].url.params["before"] == fake.candles["NVDA"][199]["timestamp"]  # nextBefore passed as given
    assert [b.day for b in got] == sorted({b.day for b in got})  # oldest first, the inclusive page edge not doubled
    assert got[0].day >= days[0] + timedelta(days=30) and got[-1].day == FRI and got[-1].close == 100.0 + 319


def test_a_daily_candle_is_its_new_york_trading_day_whatever_the_offset(fake, clock):
    fake.candles["AAPL"] = [candle("2026-10-02", 250.0), {**candle("2026-10-01", 249.0), "timestamp": "2026-10-01T13:00:00+09:00"}]  # NY midnight in KST
    got = client(fake, clock).daily_candles("AAPL", date(2026, 9, 1))
    assert [b.day for b in got] == [date(2026, 10, 1), date(2026, 10, 2)] and [b.close for b in got] == [249.0, 250.0]


# ------------------------------------------------------------------ the agreement check
def test_disagreement_needs_the_same_closes_and_the_same_market_volume():
    days = sessions(30)
    poly = series(days)
    assert disagreement(series(days, close=lambda i: (100.0 + i) * 1.0001), poly) is None  # rounding-level gaps agree
    assert "종가 불일치" in disagreement(series(days, close=lambda i: (100.0 + i) * 1.03), poly)  # e.g. dividend-adjusted
    assert "거래량" in disagreement(series(days, volume=lambda i: 50_000.0), poly)  # e.g. one broker's own volume
    assert disagreement(series(days[:4], close=lambda i: 1.0), poly[:4]) is None  # under 5 shared sessions: nothing to contradict


def test_a_session_is_complete_once_its_after_hours_have_ended():
    assert complete_session(datetime(2026, 10, 2, 19, 0, tzinfo=UTC)) == previous_trading_day(FRI)  # Fri 15:00 NY: trading
    assert complete_session(datetime(2026, 10, 2, 23, 59, tzinfo=UTC)) == previous_trading_day(FRI)  # Fri 19:59 New York: after-hours
    assert complete_session(datetime(2026, 10, 3, 0, 1, tzinfo=UTC)) == FRI  # Fri 20:01 New York
    assert complete_session(datetime(2026, 10, 5, 12, 0, tzinfo=UTC)) == FRI  # Monday morning: Friday


# ------------------------------------------------------------------ kept, preferred, refused
def test_agreeing_candles_are_kept_and_read_first_disagreeing_ones_never(fake, clock, store):
    days = sessions(260)
    store.save_bars("NVDA", series(days), "polygon")
    fake.candles["NVDA"] = to_candles(series(days, close=lambda i: (100.0 + i) * 1.0001))
    t = bars_of(fake, clock, store)
    assert t.ensure("NVDA") is True and "NVDA" in t.kept
    read = store.bars("NVDA", days[0], FRI)
    assert len(read) == 260 and read[-1].close == pytest.approx(359.0 * 1.0001, abs=0.006)  # the same session from Toss is read from Toss
    assert store.last_bars_all(days[0], FRI)["NVDA"][-1].close == pytest.approx(359.0 * 1.0001, abs=0.006)  # the scanner reads the same

    store.save_bars("AMD", series(days), "polygon")
    fake.candles["AMD"] = to_candles(series(days, close=lambda i: (100.0 + i) * 1.03))
    assert t.ensure("AMD") is False and "종가 불일치" in t.rejected["AMD"]
    assert store.bars("AMD", days[0], FRI)[-1].close == 359.0  # Polygon stays its source


def test_candles_that_stop_agreeing_are_removed_not_mixed(fake, clock, store):
    days = sessions(60)
    store.save_bars("NVDA", series(days), "polygon")
    store.save_bars("NVDA", series(days), "toss")  # kept on an earlier day
    fake.candles["NVDA"] = to_candles(series(days, volume=lambda i: 10_000.0))
    t = bars_of(fake, clock, store)
    assert t.ensure("NVDA") is False and "거래량" in t.rejected["NVDA"]
    assert store.last_bar_day("NVDA", "toss") is None


def test_the_session_still_trading_is_never_stored(fake, clock, store):
    days = sessions(40)
    store.save_bars("NVDA", series(days[:-1]), "polygon")
    fake.candles["NVDA"] = to_candles(series(days))  # Toss already has Friday's candle while Friday trades
    t = bars_of(fake, clock, store, now=datetime(2026, 10, 2, 18, 0, tzinfo=UTC))  # Friday 14:00 New York
    assert t.ensure("NVDA") is True
    assert store.last_bar_day("NVDA", "toss") == days[-2]


def test_once_per_session_then_only_the_newest_page(fake, clock, store):
    days = sessions(300)
    fake.candles["NVDA"] = to_candles(series(days[:-1]))
    now = [datetime(2026, 10, 2, 18, 0, tzinfo=UTC)]  # Friday afternoon: Thursday is the newest complete session
    t = TossBars(broker(fake, clock), store, lambda: now[0], clock=clock)
    assert t.ensure("NVDA") and t.ensure("NVDA") and t.ensure("nvda")
    first = len(candle_requests(fake))
    assert first == 2  # the full history (~420 days) once
    fake.candles["NVDA"] = to_candles(series(days))
    now[0] = SAT_MORNING_KST  # Friday's after-hours ended
    assert t.ensure("NVDA")
    new = candle_requests(fake)[first:]
    assert len(new) == 1 and int(new[0].url.params["count"]) <= 30  # just the newest sessions
    assert store.last_bar_day("NVDA", "toss") == FRI


def test_a_name_toss_does_not_know_is_not_asked_again_that_session(fake, clock, store):
    t = bars_of(fake, clock, store)
    assert t.ensure("ZZZZ") is False and t.rejected["ZZZZ"] == "토스에 없는 종목"
    n = len(candle_requests(fake))
    assert t.ensure("ZZZZ") is False and len(candle_requests(fake)) == n


def test_a_rate_limit_or_a_key_problem_pauses_every_request(fake, clock, store):
    days = sessions(30)
    for s in ("AAA", "BBB", "CCC"):
        fake.candles[s] = to_candles(series(days))
    t = bars_of(fake, clock, store, names=lambda: ["AAA", "BBB", "CCC"])
    t.ensure("AAA")  # token issued
    fake.faults = [httpx.Response(429, json={"error": {"requestId": "r", "code": "too-many-requests", "message": ""}}, headers={"Retry-After": "60"})]
    assert t.refresh() == 1  # AAA was current; BBB hit the limit; CCC was not asked
    assert t.error["kind"] == "RATE_LIMITED" and not t.due() and t.refreshed_for is None
    assert not any(r.url.params.get("symbol") == "CCC" for r in candle_requests(fake))
    clock.t += tb.PAUSE_RATE_S + 1
    assert t.due() and t.refresh() == 3 and t.refreshed_for == FRI and not t.due()

    fake.ip_allowed = False
    t2 = bars_of(fake, clock, store)
    assert t2.ensure("DDD") is False and t2.error["kind"] == "IP_NOT_ALLOWED"
    n = len(fake.requests)
    assert t2.ensure("EEE") is False and len(fake.requests) == n  # paused: nothing asked


def test_names_outside_the_list_are_bounded_per_session(fake, clock, store, monkeypatch):
    monkeypatch.setattr(tb, "EXTRA_PER_SESSION", 2)
    days = sessions(10)
    for s in ("AAA", "BBB", "CCC", "HELD"):
        fake.candles[s] = to_candles(series(days))
    t = bars_of(fake, clock, store, names=lambda: ["HELD"])
    t.refresh()
    assert t.ensure("AAA") and t.ensure("BBB") and t.ensure("CCC") is False  # the third other name waits for the next session
    assert t.ensure("HELD")


def test_not_connected_asks_nothing_and_polygon_stays(fake, clock, store):
    t = TossBars(broker(fake, clock, enabled=False), store, lambda: SAT_MORNING_KST, names=lambda: ["NVDA"], clock=clock)
    assert t.ensure("NVDA") is False and t.refresh() == 0 and not t.due() and fake.requests == []
    assert TossBars(None, store, lambda: SAT_MORNING_KST).ensure("NVDA") is False


# ------------------------------------------------------------------ through DataAccess
class NoProvider:
    def chain(self, name):  # the per-ticker Polygon request must not be needed when Toss answered
        raise AssertionError(f"provider chain {name} called")


def test_a_stock_page_read_gets_toss_history_without_a_polygon_request(fake, clock, store):
    days = sessions(260)
    fake.candles["NVDA"] = to_candles(series(days))
    t = bars_of(fake, clock, store)
    da = DataAccess(NoProvider(), {}, store=store, now_fn=lambda: SAT_MORNING_KST)
    da.daily_bars = t.ensure
    f = da.bars("NVDA", FRI - timedelta(days=300), FRI)
    assert f.provider == "store" and f.value[-1].day == FRI and f.value[-1].close == 359.0
    assert da.bars("NVDA", FRI - timedelta(days=30), FRI, fill_gaps=False).value[-1].close == 359.0
    assert da.refresh_daily("ABC~1") is False  # an archived name is never asked
