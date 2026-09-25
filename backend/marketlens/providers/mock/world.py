"""Deterministic synthetic market used in MOCK mode.

Everything produced here is fake and tagged ``DataMode.MOCK`` / source ``mock``. Company names carry a
"(MOCK)" suffix. Well-known tickers are included only so the pipeline can be exercised end to end —
their numbers are synthetic and must never be read as real market data.
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from marketlens.domain.enums import Exchange
from marketlens.domain.market_calendar import (
    UTC,
    is_trading_day,
    last_completed_session,
)

SECTOR_TEMPLATES: list[tuple[str, str]] = [
    ("Technology", "Semiconductors"),
    ("Technology", "Software—Infrastructure"),
    ("Technology", "Software—Application"),
    ("Technology", "Communication Equipment"),
    ("Communication Services", "Internet Content & Information"),
    ("Consumer Cyclical", "Internet Retail"),
    ("Consumer Cyclical", "Restaurants"),
    ("Consumer Defensive", "Household & Personal Products"),
    ("Financial Services", "Banks—Diversified"),
    ("Financial Services", "Insurance—Diversified"),
    ("Healthcare", "Drug Manufacturers—General"),
    ("Healthcare", "Biotechnology"),
    ("Healthcare", "Medical Devices"),
    ("Energy", "Oil & Gas Integrated"),
    ("Industrials", "Aerospace & Defense"),
    ("Industrials", "Electrical Equipment & Parts"),
    ("Real Estate", "REIT—Industrial"),
    ("Utilities", "Utilities—Regulated Electric"),
]

# (ticker, name, exchange, sector, industry, is_adr, country, target_mcap_bn)
NAMED: list[tuple[str, str, Exchange, str, str, bool, str, float]] = [
    ("NVDA", "NVIDIA", Exchange.NASDAQ, "Technology", "Semiconductors", False, "US", 4000),
    ("AMD", "Advanced Micro Devices", Exchange.NASDAQ, "Technology", "Semiconductors", False, "US", 300),
    ("TSM", "Taiwan Semiconductor ADR", Exchange.NYSE, "Technology", "Semiconductors", True, "TW", 1200),
    ("ASML", "ASML Holding ADR", Exchange.NASDAQ, "Technology", "Semiconductor Equipment & Materials", True, "NL", 300),
    ("ARM", "Arm Holdings ADR", Exchange.NASDAQ, "Technology", "Semiconductors", True, "GB", 150),
    ("MSFT", "Microsoft", Exchange.NASDAQ, "Technology", "Software—Infrastructure", False, "US", 3700),
    ("AMZN", "Amazon.com", Exchange.NASDAQ, "Consumer Cyclical", "Internet Retail", False, "US", 2300),
    ("GOOGL", "Alphabet", Exchange.NASDAQ, "Communication Services", "Internet Content & Information", False, "US", 2500),
    ("META", "Meta Platforms", Exchange.NASDAQ, "Communication Services", "Internet Content & Information", False, "US", 1800),
    ("AAPL", "Apple", Exchange.NASDAQ, "Technology", "Consumer Electronics", False, "US", 3500),
    ("ANET", "Arista Networks", Exchange.NYSE, "Technology", "Computer Hardware", False, "US", 170),
    ("VRT", "Vertiv Holdings", Exchange.NYSE, "Industrials", "Electrical Equipment & Parts", False, "US", 50),
    ("JPM", "JPMorgan Chase", Exchange.NYSE, "Financial Services", "Banks—Diversified", False, "US", 800),
    ("BAC", "Bank of America", Exchange.NYSE, "Financial Services", "Banks—Diversified", False, "US", 350),
    ("XOM", "Exxon Mobil", Exchange.NYSE, "Energy", "Oil & Gas Integrated", False, "US", 480),
    ("CVX", "Chevron", Exchange.NYSE, "Energy", "Oil & Gas Integrated", False, "US", 280),
    ("NVO", "Novo Nordisk ADR", Exchange.NYSE, "Healthcare", "Drug Manufacturers—General", True, "DK", 250),
    ("LLY", "Eli Lilly", Exchange.NYSE, "Healthcare", "Drug Manufacturers—General", False, "US", 700),
    ("PLD", "Prologis", Exchange.NYSE, "Real Estate", "REIT—Industrial", False, "US", 100),
    ("NEE", "NextEra Energy", Exchange.NYSE, "Utilities", "Utilities—Regulated Electric", False, "US", 150),
    ("SPY", "SPDR S&P 500 ETF", Exchange.NYSE, "ETF", "Exchange Traded Fund", False, "US", 600),
    ("QQQ", "Invesco QQQ ETF", Exchange.NASDAQ, "ETF", "Exchange Traded Fund", False, "US", 350),
]

HISTORY_DAYS = 300
# Price paths start at a fixed anchor so that worlds created at different "now" values share the same
# history prefix (enables consistent point-in-time simulation in MOCK mode).
ANCHOR = date(2024, 6, 3)


def _rng(seed: int, key: str) -> random.Random:
    return random.Random(seed * 1_000_003 + zlib.crc32(key.encode()))


@dataclass(frozen=True)
class MockSecuritySpec:
    ticker: str
    name: str
    exchange: Exchange
    sector: str
    industry: str
    is_adr: bool
    is_etf: bool
    country: str
    shares: float
    start_price: float
    drift: float
    vol: float
    base_volume: float
    growth: float  # revenue growth YoY
    gross_margin: float
    op_margin: float
    quality: float  # 0..1 latent quality driving fundamentals and revisions
    delisted_at: date | None = None


class MockWorld:
    """A reproducible synthetic US market."""

    def __init__(self, seed: int = 7, now: datetime | None = None, universe_size: int = 600) -> None:
        self.seed = seed
        self.specs: dict[str, MockSecuritySpec] = {}
        self._bars_cache: dict[str, list[tuple[date, float, float, float, float, float]]] = {}
        self.set_now(now or datetime.now(tz=UTC))
        self._build_universe(universe_size)

    def set_now(self, now: datetime) -> None:
        """Move the synthetic clock (MOCK simulation only). History before ``now`` never changes."""
        self.now = now
        self.last_session = last_completed_session(now)

    # ------------------------------------------------------------------ universe
    def _spec(self, ticker: str, name: str, exch: Exchange, sector: str, industry: str, is_adr: bool, country: str, mcap_bn: float | None, is_etf: bool = False) -> MockSecuritySpec:
        r = _rng(self.seed, ticker)
        quality = r.random()
        if mcap_bn is None:
            tier = r.random()
            mcap_bn = 0.2 + 3 * tier if tier < 0.2 else (1.5 + 60 * tier**3)
        price = math.exp(r.uniform(math.log(8), math.log(600)))
        if r.random() < 0.04 and not is_etf:
            price = r.uniform(1.0, 4.5)  # penny stock (must be filtered in stage 1)
        shares = mcap_bn * 1e9 / price
        vol = r.uniform(0.18, 0.65) if sector not in ("Utilities", "Consumer Defensive") else r.uniform(0.12, 0.3)
        turnover = r.uniform(0.002, 0.012) if r.random() > 0.05 else r.uniform(0.00005, 0.0003)
        base_volume = shares * turnover
        sector_growth = {"Technology": 0.15, "Communication Services": 0.12, "Healthcare": 0.08, "Energy": 0.02, "Utilities": 0.05}.get(sector, 0.07)
        growth = sector_growth + (quality - 0.5) * 0.35 + r.gauss(0, 0.05)
        gm = {"Technology": 0.6, "Communication Services": 0.6, "Healthcare": 0.65, "Energy": 0.35, "Utilities": 0.45, "Real Estate": 0.7, "Financial Services": 0.9}.get(sector, 0.4)
        gm = max(0.1, min(0.92, gm + (quality - 0.5) * 0.2 + r.gauss(0, 0.04)))
        om = max(-0.3, min(0.6, gm * 0.45 + (quality - 0.5) * 0.25 + r.gauss(0, 0.04)))
        drift = (quality - 0.45) * 0.35
        delisted = None
        if ticker.startswith("MK") and r.random() < 0.01:
            delisted = self.last_session - timedelta(days=r.randint(30, 200))
        return MockSecuritySpec(ticker, f"{name} (MOCK)", exch, sector, industry, is_adr, is_etf, country, shares, price, drift, vol, base_volume, growth, gm, om, quality, delisted)

    def _build_universe(self, n: int) -> None:
        for t, name, ex, sec, ind, adr, ctry, mc in NAMED:
            is_etf = sec == "ETF"
            self.specs[t] = self._spec(t, name, ex, sec, ind, adr, ctry, mc, is_etf)
        r = _rng(self.seed, "universe")
        exchanges = [Exchange.NASDAQ, Exchange.NYSE, Exchange.NYSE_AMERICAN]
        for i in range(max(0, n - len(NAMED))):
            sec, ind = SECTOR_TEMPLATES[i % len(SECTOR_TEMPLATES)]
            t = f"MK{i:04d}"
            ex = exchanges[0 if r.random() < 0.5 else (1 if r.random() < 0.85 else 2)]
            self.specs[t] = self._spec(t, f"Mock Company {i:04d}", ex, sec, ind, False, "US", None)

    # ------------------------------------------------------------------ prices
    def trading_days(self, end: date, n: int) -> list[date]:
        days: list[date] = []
        d = end
        while len(days) < n:
            if is_trading_day(d):
                days.append(d)
            d -= timedelta(days=1)
        return list(reversed(days))

    def _full_path(self, ticker: str) -> list[tuple[date, float, float, float, float, float]]:
        if ticker in self._bars_cache:
            return self._bars_cache[ticker]
        s = self.specs[ticker]
        r = _rng(self.seed, ticker + ":bars")
        mkt = _rng(self.seed, "market")
        horizon_end = max(self.last_session, date.today()) + timedelta(days=400)
        days: list[date] = []
        d = ANCHOR
        while d <= horizon_end:
            if is_trading_day(d):
                days.append(d)
            d += timedelta(days=1)
        dvol = s.vol / math.sqrt(252)
        mu = s.drift / 252
        beta = 0.6 + s.vol
        px = s.start_price
        out: list[tuple[date, float, float, float, float, float]] = []
        for day in days:
            m = mkt.gauss(0.0004, 0.009)
            ret = mu + beta * (m - 0.0004) + r.gauss(0, dvol * 0.8)
            o = px * (1 + r.gauss(0, dvol * 0.3))
            c = px * math.exp(ret)
            hi = max(o, c) * (1 + abs(r.gauss(0, dvol * 0.5)))
            lo = min(o, c) * (1 - abs(r.gauss(0, dvol * 0.5)))
            v = s.base_volume * math.exp(r.gauss(0, 0.35))
            out.append((day, round(o, 4), round(hi, 4), round(lo, 4), round(c, 4), round(v)))
            px = c
        self._bars_cache[ticker] = out
        return out

    def bars(self, ticker: str) -> list[tuple[date, float, float, float, float, float]]:
        """Bars visible at the world's current time (never beyond the last completed session)."""
        s = self.specs[ticker]
        end = min(s.delisted_at, self.last_session) if s.delisted_at else self.last_session
        return [b for b in self._full_path(ticker) if b[0] <= end]
