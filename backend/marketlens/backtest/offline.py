"""The provider registry of a backtest run: no provider reaches the network.

- price: the close of t's session from the backtest store (a quote stamped at that session's close — 16:00 New York,
  or the early close); bars come from the store;
- macro: the app's own FRED provider replaying the ALFRED answers recorded by the collector (a request that was not
  recorded fails and the macro input is MISSING — never fetched);
- every other kind: ``BlockedProvider`` — it raises ProviderUnavailable for every call and counts the attempts, so the
  inputs without a verifiable history (consensus, earnings calendar, news, options, short interest, insider) are
  MISSING exactly as when the app's provider is down.

``no_network()`` additionally refuses every socket connection for the duration of a run (leak check 4).
"""

from __future__ import annotations

import socket
from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy.engine import Engine

from marketlens.backtest.schema import ReplayTransport
from marketlens.backtest.store import BacktestStore
from marketlens.domain.enums import DataMode, TradingSession
from marketlens.domain.market import Quote
from marketlens.domain.market_calendar import session_close_utc
from marketlens.infrastructure.health import HealthRegistry
from marketlens.providers.contracts import PROVIDER_KINDS, NotSupported, ProviderUnavailable
from marketlens.infrastructure.resilience import BreakerConfig
from marketlens.providers.router import ProviderChain

REPLAY_KEY = "replay-no-network"  # the FRED provider needs *a* key to be configured; the replay strips it from lookups


class LocalQuoteProvider:
    name = "backtest-close"
    mode = DataMode.LIVE
    configured = True

    def __init__(self, store: BacktestStore) -> None:
        self.store = store

    def get_quote(self, ticker: str) -> Quote:
        ln = self.store.lineage(ticker)
        bars = self.store.bars(ticker, self.store.session, self.store.session) if ln is not None else []
        if not bars:
            raise ProviderUnavailable(f"{ticker}: {self.store.session.isoformat()} 종가 없음(그날 거래 없음)")
        b = bars[-1]
        return Quote(ticker=ticker, price=b.close, timestamp=session_close_utc(b.day), session=TradingSession.CLOSED, source=self.name,
                     mode=DataMode.LIVE, open=b.open, high=b.high, low=b.low, volume=b.volume)

    def get_daily_bars(self, *_a: Any, **_k: Any) -> Any:
        raise NotSupported("backtest: bars come from the backtest store only")


class BlockedProvider:
    """Stands in for every provider without a verifiable history: each call raises (and is counted)."""

    mode = DataMode.LIVE
    configured = True

    def __init__(self, kind: str) -> None:
        self.name = f"blocked-{kind}"
        self.kind = kind
        self.attempts = 0

    def __getattr__(self, item: str) -> Any:
        if item.startswith("get_") or item.startswith("list_"):
            def _blocked(*_a: Any, **_k: Any) -> Any:
                self.attempts += 1
                raise ProviderUnavailable(f"{self.kind}: 백테스트 — 과거 시점 값을 검증할 수 없어 사용하지 않음(네트워크 차단)")

            return _blocked
        raise AttributeError(item)


def build_offline_registry(eng: Engine, store: BacktestStore) -> tuple[Any, ReplayTransport, dict[str, BlockedProvider]]:
    from marketlens.application.registry import ProviderRegistry
    from marketlens.providers.live.fred import FredMacroProvider

    replay = ReplayTransport(eng)
    fred = FredMacroProvider(REPLAY_KEY, transport=replay, rate_per_s=1e6)
    blocked = {k: BlockedProvider(k) for k in PROVIDER_KINDS if k not in ("price", "macro")}
    health = HealthRegistry()
    impl: dict[str, list[Any]] = {k: [p] for k, p in blocked.items()}
    impl["price"] = [LocalQuoteProvider(store)]
    impl["macro"] = [fred]
    # a breaker that never opens: the chains answer from the stored point-in-time data, so a name without a quote
    # at t is that name's answer, never a reason to skip the next names. The default breaker opened after five such
    # misses for 60 s of wall-clock time — which names lost their price then depended on the runner's speed and
    # where the legs resumed, and the 7-year run and its reproduction differed (entry R/R coverage, 2026-10-04).
    never = BreakerConfig(failure_threshold=1 << 62)
    chains = {k: ProviderChain(k, impl[k], DataMode.LIVE, health, breaker_cfg=never, sleep=lambda _s: None) for k in PROVIDER_KINDS}
    return ProviderRegistry(DataMode.LIVE, health, chains), replay, blocked


class SocketBlocked(OSError):
    pass


@contextmanager
def no_network() -> Iterator[list[str]]:
    """Refuse every outbound socket connection while the block is active; yields the refused addresses."""
    refused: list[str] = []
    real_connect, real_connect_ex, real_cc = socket.socket.connect, socket.socket.connect_ex, socket.create_connection

    def connect(self: socket.socket, address: Any) -> None:  # noqa: ARG001
        refused.append(str(address))
        raise SocketBlocked(f"backtest: network blocked ({address})")

    def connect_ex(self: socket.socket, address: Any) -> int:  # noqa: ARG001
        refused.append(str(address))
        raise SocketBlocked(f"backtest: network blocked ({address})")

    def create_connection(address: Any, *a: Any, **k: Any) -> socket.socket:  # noqa: ARG001
        refused.append(str(address))
        raise SocketBlocked(f"backtest: network blocked ({address})")

    socket.socket.connect, socket.socket.connect_ex, socket.create_connection = connect, connect_ex, create_connection  # type: ignore[method-assign,assignment]
    try:
        yield refused
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = real_connect, real_connect_ex, real_cc  # type: ignore[method-assign]
