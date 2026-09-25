"""Provider contracts (canonical schemas).

Every provider declares ``name`` and ``mode`` (MOCK or LIVE). The router refuses to mix modes.
Providers raise the typed errors below — never return fabricated placeholder data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol, Sequence, runtime_checkable

from marketlens.domain.catalysts import CatalystEvent
from marketlens.domain.earnings import AnalystSnapshot, EarningsReport
from marketlens.domain.enums import DataMode
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.macro import MacroSeries
from marketlens.domain.market import Bar, Quote, Security
from marketlens.domain.options import OptionsSnapshot, OwnershipSnapshot


class ProviderError(Exception):
    """Base provider failure."""


class ProviderUnavailable(ProviderError):
    """Provider not configured (no API key / no license) or unreachable."""


class RateLimited(ProviderError):
    def __init__(self, msg: str, retry_after: float | None = None) -> None:
        super().__init__(msg)
        self.retry_after = retry_after


class ProviderDataError(ProviderError):
    """Provider answered but the payload failed validation."""


class NotSupported(ProviderError):
    """The provider does not support this symbol/data type (e.g. SEC data for a foreign filer)."""


@dataclass(frozen=True, slots=True)
class NewsItem:
    news_id: str
    published_at: datetime
    title: str
    summary: str
    url: str
    source: str
    source_type: str  # OFFICIAL | WIRE | COMMERCIAL | OTHER
    tickers: tuple[str, ...]
    body: str = ""  # UNTRUSTED external text


@dataclass(frozen=True, slots=True)
class ValuationHistory:
    ticker: str
    multiple: str
    values: tuple[float, ...]
    source: str


@dataclass(frozen=True, slots=True)
class CompanyProfileExtras:
    """Sector-specific KPIs that are not in standard statements (bank CET1, REIT occupancy, …)."""

    ticker: str
    as_of: date
    source: str
    values: dict[str, float] = field(default_factory=dict)


@runtime_checkable
class Provider(Protocol):
    name: str
    mode: DataMode


class UniverseProvider(Provider, Protocol):
    def list_securities(self, as_of: date | None = None) -> list[Security]: ...


class PriceProvider(Provider, Protocol):
    def get_quote(self, ticker: str) -> Quote: ...

    def get_daily_bars(self, ticker: str, start: date, end: date) -> list[Bar]: ...


class FundamentalProvider(Provider, Protocol):
    def get_quarterly(self, ticker: str) -> list[QuarterlyFinancials]: ...

    def get_extras(self, ticker: str) -> CompanyProfileExtras: ...


class AnalystProvider(Provider, Protocol):
    def get_estimates(self, ticker: str, as_of: date) -> AnalystSnapshot: ...

    def get_earnings_history(self, ticker: str) -> list[EarningsReport]: ...

    def get_valuation_history(self, ticker: str, multiple: str) -> ValuationHistory: ...


class NewsProvider(Provider, Protocol):
    def get_news(self, since: datetime, tickers: Sequence[str] | None = None) -> list[NewsItem]: ...


class MacroProvider(Provider, Protocol):
    def get_series(self, series_ids: Sequence[str], as_of: datetime) -> dict[str, MacroSeries]: ...


class OptionsProvider(Provider, Protocol):
    def get_options(self, ticker: str) -> OptionsSnapshot: ...


class ShortInterestProvider(Provider, Protocol):
    def get_short_interest(self, ticker: str) -> OwnershipSnapshot: ...


class InsiderProvider(Provider, Protocol):
    def get_insider(self, ticker: str) -> OwnershipSnapshot: ...


class InstitutionalProvider(Provider, Protocol):
    def get_institutional(self, ticker: str) -> OwnershipSnapshot: ...


class CalendarProvider(Provider, Protocol):
    def get_events(self, start: date, end: date) -> list[CatalystEvent]: ...


PROVIDER_KINDS = (
    "universe", "price", "fundamental", "analyst", "news", "macro", "options",
    "short_interest", "insider", "institutional", "calendar",
)
