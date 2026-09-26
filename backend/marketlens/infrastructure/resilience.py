"""Circuit breaker, retry with exponential backoff + jitter, and a token-bucket rate limiter.

Clocks and sleep functions are injectable so behaviour is fully testable.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Callable, TypeVar

from marketlens.domain.enums import BreakerState
from marketlens.providers.contracts import ProviderDataError, ProviderUnavailable, RateLimited, NotSupported

T = TypeVar("T")


class CircuitOpen(ProviderUnavailable):
    pass


@dataclass
class BreakerConfig:
    failure_threshold: int = 5
    reset_timeout_s: float = 60.0
    half_open_max_calls: int = 1


class CircuitBreaker:
    def __init__(self, name: str, cfg: BreakerConfig | None = None, clock: Callable[[], float] = time.monotonic) -> None:
        self.name = name
        self.cfg = cfg or BreakerConfig()
        self._clock = clock
        self._state = BreakerState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_calls = 0
        self._lock = threading.Lock()

    @property
    def state(self) -> BreakerState:
        with self._lock:
            if self._state == BreakerState.OPEN and self._clock() - self._opened_at >= self.cfg.reset_timeout_s:
                self._state = BreakerState.HALF_OPEN
                self._half_open_calls = 0
            return self._state

    def allow(self) -> bool:
        st = self.state
        with self._lock:
            if st == BreakerState.CLOSED:
                return True
            if st == BreakerState.HALF_OPEN and self._half_open_calls < self.cfg.half_open_max_calls:
                self._half_open_calls += 1
                return True
            return False

    def reset(self) -> None:
        """Back to CLOSED (used by the live smoke test so each category is judged by its own request)."""
        with self._lock:
            self._state, self._failures, self._half_open_calls = BreakerState.CLOSED, 0, 0

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._state = BreakerState.CLOSED

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._state == BreakerState.HALF_OPEN or self._failures >= self.cfg.failure_threshold:
                self._state = BreakerState.OPEN
                self._opened_at = self._clock()

    def call(self, fn: Callable[[], T]) -> T:
        if not self.allow():
            raise CircuitOpen(f"circuit open for {self.name}")
        try:
            result = fn()
        except (NotSupported, ProviderDataError):
            # the provider answered; data problems are not availability failures
            self.record_success()
            raise
        except Exception:
            self.record_failure()
            raise
        self.record_success()
        return result


@dataclass
class RetryConfig:
    attempts: int = 3
    base_delay_s: float = 0.5
    max_delay_s: float = 8.0
    jitter: float = 0.25


RETRYABLE = (ProviderUnavailable, RateLimited, TimeoutError, ConnectionError)


def backoff_delay(attempt: int, cfg: RetryConfig, rng: random.Random | None = None) -> float:
    r = rng or random
    d = min(cfg.max_delay_s, cfg.base_delay_s * (2 ** attempt))
    return d * (1 + r.uniform(-cfg.jitter, cfg.jitter))


def retry(fn: Callable[[], T], cfg: RetryConfig | None = None, sleep: Callable[[float], None] = time.sleep, rng: random.Random | None = None) -> T:
    cfg = cfg or RetryConfig()
    last: Exception | None = None
    for attempt in range(cfg.attempts):
        try:
            return fn()
        except CircuitOpen:
            raise
        except RateLimited as e:
            last = e
            if attempt == cfg.attempts - 1:
                break
            wait = e.retry_after if e.retry_after is not None else backoff_delay(attempt, cfg, rng)
            if wait > cfg.max_delay_s:
                raise  # respect long Retry-After windows: don't hammer early, let health mark RATE_LIMITED
            sleep(wait)
        except RETRYABLE as e:
            last = e
            if attempt == cfg.attempts - 1:
                break
            sleep(backoff_delay(attempt, cfg, rng))
    assert last is not None
    raise last


class TokenBucket:
    """Client-side rate limiting so provider quotas are respected."""

    def __init__(self, rate_per_s: float, capacity: int, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep) -> None:
        self.rate = rate_per_s
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last = clock()
        self._clock = clock
        self._sleep = sleep
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = self._clock()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                wait = (1 - self._tokens) / self.rate
            self._sleep(wait)
