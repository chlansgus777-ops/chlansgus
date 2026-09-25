"""Every provider must return canonical domain types (or raise typed errors) — never ad-hoc dicts."""

from datetime import date, datetime, timedelta, timezone

import httpx
import pytest

from marketlens.domain.catalysts import CatalystEvent
from marketlens.domain.earnings import AnalystSnapshot, EarningsReport
from marketlens.domain.enums import DataMode, DataQuality
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.macro import ALL_SERIES, US10Y, MacroSeries
from marketlens.domain.market import Bar, Quote, Security
from marketlens.domain.options import OptionsSnapshot, OwnershipSnapshot
from marketlens.providers.contracts import NewsItem, NotSupported, ProviderDataError, ProviderUnavailable, RateLimited
from marketlens.providers.live.finnhub import FinnhubProvider
from marketlens.providers.live.fred import FredMacroProvider
from marketlens.providers.live.polygon import PolygonProvider
from marketlens.providers.live.sec_edgar import SecEdgarProvider, parse_company_facts
from marketlens.providers.live.unavailable import UnavailableProvider
from marketlens.providers.mock import providers as m
from marketlens.providers.mock.world import MockWorld

NOW = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def world():
    return MockWorld(now=NOW, universe_size=80)


def test_mock_contracts(world):
    secs = m.MockUniverseProvider(world).list_securities()
    assert secs and all(isinstance(s, Security) for s in secs)
    assert all(s.company_name.endswith("(MOCK)") for s in secs)
    q = m.MockPriceProvider(world).get_quote("NVDA")
    assert isinstance(q, Quote) and q.mode == DataMode.MOCK and q.source == "mock"
    bars = m.MockPriceProvider(world).get_daily_bars("NVDA", date(2026, 1, 1), NOW.date())
    assert bars and all(isinstance(b, Bar) for b in bars) and bars[-1].day < NOW.date()
    qs = m.MockFundamentalProvider(world).get_quarterly("JPM")
    assert all(isinstance(x, QuarterlyFinancials) and x.filed_date <= NOW.date() for x in qs)
    assert isinstance(m.MockAnalystProvider(world).get_estimates("NVDA", NOW.date()), AnalystSnapshot)
    assert all(isinstance(x, EarningsReport) for x in m.MockAnalystProvider(world).get_earnings_history("NVDA"))
    assert all(isinstance(x, NewsItem) for x in m.MockNewsProvider(world).get_news(NOW - timedelta(days=5)))
    ms = m.MockMacroProvider(world).get_series(ALL_SERIES, NOW)
    assert all(isinstance(v, MacroSeries) and v.latest.mode == DataMode.MOCK for v in ms.values())
    assert isinstance(m.MockOptionsProvider(world).get_options("NVDA"), OptionsSnapshot)
    assert isinstance(m.MockOwnershipProvider(world).get_short_interest("NVDA"), OwnershipSnapshot)
    assert all(isinstance(e, CatalystEvent) for e in m.MockCalendarProvider(world).get_events(NOW.date(), NOW.date() + timedelta(days=60)))
    with pytest.raises(NotSupported):
        m.MockFundamentalProvider(world).get_quarterly("SPY")


def test_mock_history_is_stable_when_clock_moves():
    w1 = MockWorld(now=NOW - timedelta(days=30), universe_size=30)
    w2 = MockWorld(now=NOW, universe_size=30)
    b1 = m.MockPriceProvider(w1).get_daily_bars("NVDA", date(2026, 1, 1), NOW.date())
    b2 = m.MockPriceProvider(w2).get_daily_bars("NVDA", date(2026, 1, 1), NOW.date())
    assert b1 == b2[: len(b1)] and len(b2) > len(b1)
    q1 = m.MockFundamentalProvider(w1).get_quarterly("NVDA")
    q2 = m.MockFundamentalProvider(w2).get_quarterly("NVDA")
    assert {x.period_end: x.revenue for x in q1}.items() <= {x.period_end: x.revenue for x in q2}.items()


def transport(routes):
    def handler(req: httpx.Request) -> httpx.Response:
        for key, resp in routes.items():
            if key in str(req.url):
                return resp(req) if callable(resp) else resp
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def test_sec_universe_and_facts():
    tickers = {"fields": ["cik", "name", "ticker", "exchange"], "data": [[1045810, "NVIDIA CORP", "NVDA", "Nasdaq"], [19617, "JPMORGAN", "JPM", "NYSE"], [1, "OTC CO", "OTCX", "OTC"]]}

    def fact(val, end, start, filed, form="10-Q", fp="Q1"):
        return {"val": val, "end": end, "start": start, "filed": filed, "form": form, "fy": 2026, "fp": fp}

    facts = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [fact(100, "2026-03-31", "2026-01-01", "2026-05-01"), fact(110, "2026-06-30", "2026-04-01", "2026-08-01"),
                                       fact(90, "2026-03-31", "2026-01-01", "2026-09-01")]}},  # later restatement must NOT overwrite
        "NetIncomeLoss": {"units": {"USD": [fact(10, "2026-03-31", "2026-01-01", "2026-05-01"), fact(12, "2026-06-30", "2026-04-01", "2026-08-01")]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [fact(20, "2026-03-31", "2026-01-01", "2026-05-01"), fact(50, "2026-06-30", "2026-01-01", "2026-08-01", fp="Q2")]}},
    }}}
    t = transport({"company_tickers_exchange": httpx.Response(200, json=tickers), "companyfacts/CIK0001045810": httpx.Response(200, json=facts)})
    p = SecEdgarProvider("MarketLens test test@example.com", transport=t)
    secs = p.list_securities()
    assert [s.ticker for s in secs] == ["NVDA", "JPM"] and secs[0].sector == "Unknown" and secs[0].market_cap is None
    qs = p.get_quarterly("NVDA")
    assert [q.revenue for q in qs] == [100, 110]  # point-in-time: original filing kept
    assert qs[0].filed_date == date(2026, 5, 1)
    assert qs[1].operating_cash_flow == 30  # YTD 50 − Q1 20
    with pytest.raises(NotSupported):
        p.get_quarterly("ZZZZ")


def test_sec_requires_user_agent_and_foreign_filer_not_supported():
    with pytest.raises(ProviderUnavailable):
        SecEdgarProvider(None).list_securities()
    with pytest.raises(NotSupported):
        parse_company_facts({"facts": {"ifrs-full": {}}}, "TSM")


def test_fred_parsing_missing_values_and_staleness():
    obs = {"observations": [{"date": (date(2026, 9, 24) - timedelta(days=i)).isoformat(), "value": "." if i == 1 else str(4.0 + i * 0.01)} for i in reversed(range(30))]}
    t = transport({"series/observations": httpx.Response(200, json=obs)})
    p = FredMacroProvider("k", transport=t)
    out = p.get_series([US10Y], NOW)
    s = out[US10Y]
    assert s.latest.value == pytest.approx(4.0) and s.latest.quality == DataQuality.FRESH and s.latest.source == "fred:DGS10"
    assert s.change_20d is not None
    with pytest.raises(ProviderUnavailable):
        FredMacroProvider(None).get_series([US10Y], NOW)


def test_finnhub_quote_news_and_errors():
    ts = int(NOW.timestamp())
    t = transport({"/quote": httpx.Response(200, json={"c": 101.5, "t": ts, "o": 100, "h": 102, "l": 99, "pc": 100}),
                   "/company-news": httpx.Response(200, json=[{"id": 1, "datetime": ts, "headline": "h", "summary": "s", "url": "u", "source": "wire", "related": "NVDA"}])})
    p = FinnhubProvider("k", transport=t)
    q = p.get_quote("NVDA")
    assert q.price == 101.5 and q.timestamp == NOW and q.mode == DataMode.LIVE
    assert p.get_news(NOW - timedelta(days=1), ["NVDA"])[0].tickers == ("NVDA",)
    with pytest.raises(NotSupported):
        p.get_daily_bars("NVDA", date(2026, 1, 1), date(2026, 2, 1))
    rl = FinnhubProvider("k", transport=transport({"/quote": httpx.Response(429, headers={"Retry-After": "7"})}))
    with pytest.raises(RateLimited) as e:
        rl.get_quote("NVDA")
    assert e.value.retry_after == 7
    bad = FinnhubProvider("k", transport=transport({"/quote": httpx.Response(200, json={"c": 0, "t": 0})}))
    with pytest.raises(ProviderDataError):
        bad.get_quote("NVDA")
    auth = FinnhubProvider("k", transport=transport({"/quote": httpx.Response(401)}))
    with pytest.raises(ProviderUnavailable):
        auth.get_quote("NVDA")


def test_polygon_bars():
    ms = int(datetime(2026, 9, 24, 4, 0, tzinfo=timezone.utc).timestamp() * 1000)
    t = transport({"/v2/aggs/ticker/NVDA": httpx.Response(200, json={"results": [{"t": ms, "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}]}),
                   "/grouped/": httpx.Response(200, json={"results": [{"T": "NVDA", "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 100}, {"T": "BAD"}]})})
    p = PolygonProvider("k", transport=t)
    b = p.get_daily_bars("NVDA", date(2026, 9, 1), date(2026, 9, 25))
    assert b == [Bar(date(2026, 9, 24), 1, 2, 0.5, 1.5, 100)]
    assert list(p.get_grouped_daily(date(2026, 9, 24))) == ["NVDA"]


def test_unavailable_provider_never_returns_data():
    u = UnavailableProvider("options", "not licensed")
    assert u.mode == DataMode.LIVE and not u.configured
    with pytest.raises(ProviderUnavailable):
        u.get_options("NVDA")
