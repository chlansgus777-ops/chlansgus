"""Build provider chains for the configured mode. MOCK and LIVE are never mixed."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from marketlens.config import Settings
from marketlens.domain.enums import DataMode
from marketlens.infrastructure.health import HealthRegistry
from marketlens.providers.contracts import PROVIDER_KINDS
from marketlens.providers.router import ProviderChain


@dataclass
class ProviderRegistry:
    mode: DataMode
    health: HealthRegistry
    chains: dict[str, ProviderChain]
    world: object | None = None  # MockWorld in MOCK mode

    def chain(self, kind: str) -> ProviderChain:
        return self.chains[kind]

    def describe(self) -> list[dict[str, object]]:
        out = []
        for kind, ch in self.chains.items():
            for p in ch.providers:
                out.append({"kind": kind, "provider": p.name, "mode": p.mode.value, "configured": getattr(p, "configured", True), "reason": getattr(p, "reason", None)})
        return out


def build_mock_registry(health: HealthRegistry | None = None, now: datetime | None = None, universe_size: int = 600, seed: int = 7) -> ProviderRegistry:
    from marketlens.providers.mock import providers as m
    from marketlens.providers.mock.world import MockWorld

    health = health or HealthRegistry()
    w = MockWorld(seed=seed, now=now, universe_size=universe_size)
    own = m.MockOwnershipProvider(w)
    impl = {
        "universe": [m.MockUniverseProvider(w)],
        "price": [m.MockPriceProvider(w)],
        "fundamental": [m.MockFundamentalProvider(w)],
        "analyst": [m.MockAnalystProvider(w)],
        "news": [m.MockNewsProvider(w)],
        "macro": [m.MockMacroProvider(w)],
        "options": [m.MockOptionsProvider(w)],
        "short_interest": [own],
        "insider": [own],
        "institutional": [own],
        "calendar": [m.MockCalendarProvider(w)],
    }
    chains = {k: ProviderChain(k, impl[k], DataMode.MOCK, health, sleep=lambda _s: None) for k in PROVIDER_KINDS}
    return ProviderRegistry(DataMode.MOCK, health, chains, world=w)


def build_live_registry(settings: Settings, health: HealthRegistry | None = None, transport: object | None = None, sleep: object | None = None) -> ProviderRegistry:
    """LIVE providers. ``transport`` (an httpx transport) is only injected by fixture/contract tests."""
    from marketlens.providers.live.alphavantage import AlphaVantageEstimatesProvider
    from marketlens.providers.live.finnhub import FinnhubProvider
    from marketlens.providers.live.finra import FinraShortInterestProvider
    from marketlens.providers.live.fred import FredMacroProvider
    from marketlens.providers.live.polygon import PolygonProvider
    from marketlens.providers.live.sec_edgar import SecEdgarProvider
    from marketlens.providers.live.unavailable import UnavailableProvider as U

    health = health or HealthRegistry()
    fast = {"rate_per_s": 1000.0} if transport is not None else {}  # fixture transports: no real quota to respect
    sec = SecEdgarProvider(settings.sec_user_agent, transport=transport, **fast)
    fin = FinnhubProvider(settings.finnhub_api_key, transport=transport, realtime=settings.finnhub_realtime, **fast)
    poly = PolygonProvider(settings.polygon_api_key, transport=transport, **fast)
    fred = FredMacroProvider(settings.fred_api_key, transport=transport, **fast)
    impl = {
        "universe": [sec],
        "price": [fin, poly],  # quotes: Finnhub; bars: Polygon (Finnhub raises NotSupported → failover)
        "fundamental": [sec],
        # earnings history + calendar consensus snapshots (Finnhub, whole market, one request) and FY
        # consensus with provider-reported 7/30/60/90-day history (Alpha Vantage, final candidates only)
        "analyst": [fin, AlphaVantageEstimatesProvider(settings.alphavantage_api_key, transport=transport, **fast)],
        "news": [fin],
        "macro": [fred],
        "options": [U("options", "무료 옵션 데이터 공급원 없음 (MISSING)")],
        "short_interest": [FinraShortInterestProvider(settings.finra_api_key, settings.finra_api_secret, transport=transport, **fast)],
        "insider": [sec],  # SEC Form 4 (free)
        "institutional": [U("institutional", "13F 기관 보유는 CUSIP 매핑이 필요해 미구현 (MISSING)")],
        "calendar": [fin],
    }
    extra = {"sleep": sleep} if sleep is not None else {}
    chains = {k: ProviderChain(k, impl[k], DataMode.LIVE, health, **extra) for k in PROVIDER_KINDS}
    return ProviderRegistry(DataMode.LIVE, health, chains)


def build_registry(settings: Settings, health: HealthRegistry | None = None) -> ProviderRegistry:
    if settings.mode == DataMode.MOCK:
        return build_mock_registry(health, universe_size=settings.mock_universe_size)
    return build_live_registry(settings, health)
