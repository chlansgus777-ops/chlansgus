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


def build_live_registry(settings: Settings, health: HealthRegistry | None = None) -> ProviderRegistry:
    from marketlens.providers.live.finnhub import FinnhubProvider
    from marketlens.providers.live.fred import FredMacroProvider
    from marketlens.providers.live.polygon import PolygonProvider
    from marketlens.providers.live.sec_edgar import SecEdgarProvider
    from marketlens.providers.live.unavailable import UnavailableProvider as U

    health = health or HealthRegistry()
    sec = SecEdgarProvider(settings.sec_user_agent)
    fin = FinnhubProvider(settings.finnhub_api_key)
    poly = PolygonProvider(settings.polygon_api_key)
    fred = FredMacroProvider(settings.fred_api_key)
    impl = {
        "universe": [sec],
        "price": [fin, poly],  # quotes: Finnhub; bars: Polygon (Finnhub raises NotSupported → failover)
        "fundamental": [sec],
        "analyst": [fin],  # earnings history only; estimates/revisions need a licensed feed
        "news": [fin],
        "macro": [fred],
        "options": [U("options", "no licensed options data provider configured")],
        "short_interest": [U("short_interest", "no licensed short-interest provider configured")],
        "insider": [U("insider", "insider data provider not configured (SEC Form 4 parser is a planned addition)")],
        "institutional": [U("institutional", "13F provider not configured")],
        "calendar": [fin],
    }
    chains = {k: ProviderChain(k, impl[k], DataMode.LIVE, health) for k in PROVIDER_KINDS}
    return ProviderRegistry(DataMode.LIVE, health, chains)


def build_registry(settings: Settings, health: HealthRegistry | None = None) -> ProviderRegistry:
    if settings.mode == DataMode.MOCK:
        return build_mock_registry(health, universe_size=settings.mock_universe_size)
    return build_live_registry(settings, health)
