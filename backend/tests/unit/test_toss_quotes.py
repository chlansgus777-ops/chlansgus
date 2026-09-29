"""토스 prices as the real-time feed (application/toss_quotes.py) — owner 2026-09-29: "실시간 가격을 받고싶다 … 정규장에서만
실시간으로 보인다는거야?". Every session Toss quotes shows live prices; an analysis prices at the same second."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from marketlens.application.broker import BrokerSync
from marketlens.application.live_quotes import EXTENDED_NO_TRADE, LIVE, QuoteHub
from marketlens.application.toss_quotes import IDLE_EVERY_S, TossQuoteFeed, live_quote, quiet_now
from marketlens.providers.live.toss import TossClient
from tests.toss_fake import FakeToss

UTC = timezone.utc
PRE = datetime(2026, 9, 29, 8, 40, tzinfo=UTC)  # Tuesday 04:40 ET: pre-market (17:40 KST)


class Clock:
    def __init__(self, t: datetime) -> None:
        self.t = t

    def __call__(self) -> datetime:
        return self.t


def world(t: datetime = PRE, n: int = 3):  # noqa: ANN201
    fake, clock = FakeToss(), Clock(t)
    hub = QuoteHub(source="finnhub", max_symbols=50, coverage_ko="test", now=clock, streaming=True)
    broker = BrokerSync(lambda: None, clock, fake.client_id, fake.secret, enabled=True, transport=fake.transport())  # type: ignore[arg-type]
    feed = TossQuoteFeed(hub, broker, now=clock)
    names = ["NVDA", "AMD", "BRK.B"][:n]
    hub.view(names)
    for i, x in enumerate(names):
        fake.prices[x] = (f"{100 + i}.25", (t - timedelta(seconds=2)).isoformat())
    return fake, clock, hub, broker, feed


def row(hub: QuoteHub, t: str) -> dict:
    return next(r for r in hub.rows(tickers=[t]) if r["ticker"] == t)


def test_pre_market_prices_are_live_every_second():
    fake, clock, hub, _b, feed = world()
    assert feed.tick() == 1.0
    r = row(hub, "NVDA")
    assert r["state"] == LIVE and r["price"] == pytest.approx(100.25) and r["source"] == "toss" and r["feed"] == "poll" and r["session"] == "PREMARKET"
    assert row(hub, "BRK.B")["price"] == pytest.approx(102.25)
    # a new print moves the row; the same print again does not
    v = hub.version
    fake.prices["NVDA"] = ("101.00", clock.t.isoformat())
    feed.tick()
    assert row(hub, "NVDA")["price"] == pytest.approx(101.0) and hub.version > v
    v = hub.version
    feed.tick()
    assert hub.version == v and feed.prints == 4
    assert fake.errors == [] and all(r.url.path in ("/oauth2/token", "/api/v1/prices") for r in fake.requests)


def test_old_print_is_marked_not_live():
    fake, clock, hub, _b, feed = world()
    fake.prices["NVDA"] = ("99.00", (clock.t - timedelta(hours=10)).isoformat())  # yesterday's close
    feed.tick()
    assert row(hub, "NVDA")["state"] == EXTENDED_NO_TRADE


def test_overnight_trading_prints_are_live_and_weekends_idle():
    night = datetime(2026, 9, 29, 3, 0, tzinfo=UTC)  # Monday 23:00 ET (12:00 KST): exchanges closed, overnight trading
    fake, clock, hub, _b, feed = world(night)
    feed.tick()
    assert row(hub, "NVDA")["state"] == LIVE
    assert quiet_now(datetime(2026, 9, 26, 15, 0, tzinfo=UTC))  # Saturday
    assert not quiet_now(datetime(2026, 9, 28, 1, 0, tzinfo=UTC))  # Sunday 21:00 ET: overnight opens
    clock.t = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)
    assert feed.tick() == IDLE_EVERY_S


def test_two_hundred_names_one_call_and_the_stream_keeps_its_fifty():
    fake, clock, hub, _b, feed = world()
    names = [f"T{i:03d}" for i in range(230)]
    hub.view(names)
    for x in names:
        fake.prices[x] = ("10.00", clock.t.isoformat())
    feed.tick()
    calls = [r for r in fake.requests if r.url.path == "/api/v1/prices"]
    assert len(calls) == 1 and fake.errors == []
    planned, over = hub.plan()
    assert len(planned) == 200 and len(over) == 33
    assert sum(1 for r in hub.rows() if r["source"] == "toss") == 200


def test_key_trouble_stops_the_feed_quietly_and_says_why():
    fake, clock, hub, _b, feed = world()
    fake.ip_allowed = False
    assert feed.tick() == 60.0
    assert feed.status()["error"]["kind"] == "IP_NOT_ALLOWED" and not feed.status()["live"]
    fake.ip_allowed = True
    feed.tick()
    assert feed.status()["error"] is None and feed.status()["live"]


def test_no_key_no_feed():
    clock = Clock(PRE)
    hub = QuoteHub(source="finnhub", max_symbols=50, coverage_ko="t", now=clock)
    feed = TossQuoteFeed(hub, BrokerSync(lambda: None, clock, None, None, enabled=True), now=clock)  # type: ignore[arg-type]
    assert feed.tick() == 5.0 and hub.poll_capacity == 0 and not feed.status()["active"]


def test_the_feed_shares_the_brokers_single_token():
    fake, clock, hub, broker, feed = world()
    feed.tick()
    broker.client.accounts()
    assert broker.client.issued == 1 and isinstance(broker.client, TossClient)


def test_an_analysis_prices_at_the_screens_second():
    fake, clock, hub, _b, feed = world()
    feed.tick()
    q = live_quote(hub, "NVDA")
    assert q is not None and q.price == pytest.approx(100.25) and q.source == "toss" and q.session.value == "PREMARKET" and q.is_realtime
    clock.t += timedelta(minutes=5)  # the feed stopped answering: never an old poll price as "current"
    assert live_quote(hub, "NVDA") is None
    assert live_quote(hub, "UNKNOWN") is None


def test_a_name_no_screen_shows_is_asked_for_at_analysis_time():
    from tests.integration.test_service_api import make_service

    fake = FakeToss()
    svc = make_service(universe=40)
    svc.attach_broker(BrokerSync(svc.sf, svc.now, fake.client_id, fake.secret, enabled=True, transport=fake.transport()))
    fake.prices["NVDA"] = ("123.45", svc.now().isoformat())
    q = svc.data.quote("NVDA")
    assert q.provider == "toss" and q.value.price == pytest.approx(123.45)
    fake.prices.clear()
    assert svc.data.quote("MSFT").provider != "toss"  # Toss has no price: the providers answer as before
