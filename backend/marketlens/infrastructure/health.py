"""Provider health tracking (HEALTHY / DEGRADED / RATE_LIMITED / STALE / DOWN)."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from marketlens.domain.enums import BreakerState, DataMode, ProviderStatus
from marketlens.domain.market_calendar import UTC


def _now() -> datetime:
    return datetime.now(tz=UTC)


@dataclass
class ProviderHealth:
    name: str
    kind: str
    mode: DataMode
    requests: int = 0
    failures: int = 0
    last_success: datetime | None = None
    last_failure: datetime | None = None
    last_error: str | None = None
    rate_limited_until: datetime | None = None
    latencies_ms: deque[float] = field(default_factory=lambda: deque(maxlen=50))
    outcomes: deque[bool] = field(default_factory=lambda: deque(maxlen=50))
    last_data_ts: datetime | None = None
    breaker_state: BreakerState = BreakerState.CLOSED
    configured: bool = True

    @property
    def error_rate(self) -> float:
        return 0.0 if not self.outcomes else sum(1 for o in self.outcomes if not o) / len(self.outcomes)

    @property
    def latency_ms(self) -> float | None:
        return sum(self.latencies_ms) / len(self.latencies_ms) if self.latencies_ms else None

    def status(self, now: datetime | None = None, stale_after: timedelta = timedelta(hours=26)) -> ProviderStatus:
        now = now or _now()
        if not self.configured:
            return ProviderStatus.DOWN
        if self.breaker_state == BreakerState.OPEN:
            return ProviderStatus.DOWN
        if self.rate_limited_until and self.rate_limited_until > now:
            return ProviderStatus.RATE_LIMITED
        if self.outcomes and self.error_rate >= 0.5:
            return ProviderStatus.DEGRADED if self.last_success else ProviderStatus.DOWN
        if self.last_data_ts is not None and now - self.last_data_ts > stale_after:
            return ProviderStatus.STALE
        if self.outcomes and self.error_rate > 0.1:
            return ProviderStatus.DEGRADED
        return ProviderStatus.HEALTHY

    def as_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "kind": self.kind,
            "mode": self.mode.value,
            "status": self.status().value,
            "configured": self.configured,
            "requests": self.requests,
            "error_rate": round(self.error_rate, 4),
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
            "last_success": self.last_success.isoformat() if self.last_success else None,
            "last_failure": self.last_failure.isoformat() if self.last_failure else None,
            "last_error": self.last_error,
            "freshness": self.last_data_ts.isoformat() if self.last_data_ts else None,
            "rate_limit_state": "LIMITED" if self.rate_limited_until and self.rate_limited_until > _now() else "OK",
            "breaker_state": self.breaker_state.value,
        }


class HealthRegistry:
    def __init__(self, on_transition: Callable[[str, ProviderStatus, ProviderStatus], None] | None = None) -> None:
        self._items: dict[str, ProviderHealth] = {}
        self._lock = threading.Lock()
        self._on_transition = on_transition

    def get(self, name: str, kind: str, mode: DataMode, configured: bool = True) -> ProviderHealth:
        with self._lock:
            if name not in self._items:
                self._items[name] = ProviderHealth(name=name, kind=kind, mode=mode, configured=configured)
            return self._items[name]

    def record(self, name: str, ok: bool, latency_ms: float, error: str | None = None, rate_limited_for: float | None = None, data_ts: datetime | None = None, breaker: BreakerState | None = None) -> None:
        with self._lock:
            h = self._items[name]
            before = h.status()
            h.requests += 1
            h.latencies_ms.append(latency_ms)
            h.outcomes.append(ok)
            if ok:
                h.last_success = _now()
                if data_ts is not None:
                    h.last_data_ts = data_ts
            else:
                h.failures += 1
                h.last_failure = _now()
                h.last_error = (error or "")[:300]
            if rate_limited_for:
                h.rate_limited_until = _now() + timedelta(seconds=rate_limited_for)
            if breaker is not None:
                h.breaker_state = breaker
            after = h.status()
        if before != after and self._on_transition:
            self._on_transition(name, before, after)

    def all(self) -> list[ProviderHealth]:
        with self._lock:
            return list(self._items.values())
