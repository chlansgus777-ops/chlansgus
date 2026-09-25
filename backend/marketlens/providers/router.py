"""Provider routing: failover, circuit breakers, retries, health and conflict detection.

- Primary failure → next provider (secondary …).
- If a cross-check is requested and two providers disagree beyond tolerance, the result is marked
  CONFLICTING. The router never silently picks one side.
- MOCK and LIVE providers can never be mixed in one chain.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Generic, Sequence, TypeVar

from marketlens.domain.enums import DataMode
from marketlens.infrastructure.health import HealthRegistry
from marketlens.infrastructure.logging import Event, log_event
from marketlens.infrastructure.resilience import BreakerConfig, CircuitBreaker, RetryConfig, retry
from marketlens.providers.contracts import NotSupported, ProviderError, ProviderUnavailable, RateLimited

T = TypeVar("T")
log = logging.getLogger("marketlens.providers")


class ModeMixError(ValueError):
    pass


class AllProvidersFailed(ProviderUnavailable):
    def __init__(self, kind: str, errors: list[tuple[str, str]]) -> None:
        super().__init__(f"all {kind} providers failed: {errors}")
        self.errors = errors


@dataclass
class Routed(Generic[T]):
    value: T
    provider: str
    retrieved_at: datetime
    conflicts: list[str] = field(default_factory=list)
    secondary: str | None = None


class ProviderChain:
    def __init__(
        self,
        kind: str,
        providers: Sequence[Any],
        mode: DataMode,
        health: HealthRegistry,
        breaker_cfg: BreakerConfig | None = None,
        retry_cfg: RetryConfig | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        for p in providers:
            if p.mode != mode:
                raise ModeMixError(f"{kind}: provider {p.name} is {p.mode}, chain is {mode} — MOCK and LIVE must never be mixed")
        self.kind = kind
        self.providers = list(providers)
        self.mode = mode
        self.health = health
        self.retry_cfg = retry_cfg or RetryConfig()
        self._sleep = sleep
        self.breakers = {p.name: CircuitBreaker(p.name, breaker_cfg, clock=clock) for p in providers}
        for p in providers:
            health.get(p.name, kind, p.mode, configured=getattr(p, "configured", True))

    @property
    def available(self) -> bool:
        return any(getattr(p, "configured", True) for p in self.providers)

    def _invoke(self, p: Any, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        br = self.breakers[p.name]
        start = time.perf_counter()
        h = self.health.get(p.name, self.kind, p.mode)
        was_failing = bool(h.outcomes) and not h.outcomes[-1]
        try:
            value = retry(lambda: br.call(lambda: getattr(p, method)(*args, **kwargs)), self.retry_cfg, sleep=self._sleep)
        except RateLimited as e:
            self.health.record(p.name, False, (time.perf_counter() - start) * 1000, str(e), rate_limited_for=e.retry_after or 60, breaker=br.state)
            raise
        except NotSupported:
            self.health.record(p.name, True, (time.perf_counter() - start) * 1000, breaker=br.state)
            raise
        except Exception as e:
            self.health.record(p.name, False, (time.perf_counter() - start) * 1000, f"{type(e).__name__}: {e}", breaker=br.state)
            log_event(log, Event.PROVIDER_FAILED, level=logging.WARNING, provider=p.name, kind=self.kind, error=type(e).__name__)
            raise
        self.health.record(p.name, True, (time.perf_counter() - start) * 1000, data_ts=datetime.now().astimezone(), breaker=br.state)
        if was_failing:
            log_event(log, Event.PROVIDER_RECOVERED, provider=p.name, kind=self.kind)
        return value

    def call(self, method: str, *args: Any, cross_check: Callable[[Any, Any], list[str]] | None = None, **kwargs: Any) -> Routed[Any]:
        errors: list[tuple[str, str]] = []
        for i, p in enumerate(self.providers):
            if not getattr(p, "configured", True):
                errors.append((p.name, "not configured"))
                continue
            try:
                value = self._invoke(p, method, args, kwargs)
            except NotSupported as e:
                errors.append((p.name, f"not supported: {e}"))
                continue
            except (ProviderError, TimeoutError, ConnectionError, OSError) as e:
                errors.append((p.name, f"{type(e).__name__}: {e}"))
                continue
            routed = Routed(value=value, provider=p.name, retrieved_at=datetime.now().astimezone())
            if cross_check is not None:
                for q in self.providers[i + 1 :]:
                    if not getattr(q, "configured", True):
                        continue
                    try:
                        other = self._invoke(q, method, args, kwargs)
                    except (ProviderError, TimeoutError, ConnectionError, OSError):
                        continue
                    routed.secondary = q.name
                    routed.conflicts = cross_check(value, other)
                    if routed.conflicts:
                        log_event(log, Event.PROVIDER_CONFLICT, level=logging.WARNING, kind=self.kind, primary=p.name, secondary=q.name, fields_=routed.conflicts)
                    break
            return routed
        raise AllProvidersFailed(self.kind, errors)


def relative_conflicts(fields: Sequence[str], tolerance: float) -> Callable[[Any, Any], list[str]]:
    """Cross-check helper: flag fields whose values differ by more than ``tolerance`` (relative)."""

    def check(a: Any, b: Any) -> list[str]:
        out: list[str] = []
        for f in fields:
            va, vb = getattr(a, f, None), getattr(b, f, None)
            if va is None or vb is None:
                continue
            denom = max(abs(va), abs(vb), 1e-12)
            if abs(va - vb) / denom > tolerance:
                out.append(f"{f}: {va} vs {vb}")
        return out

    return check
