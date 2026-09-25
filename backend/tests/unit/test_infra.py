import random
from datetime import datetime, timedelta, timezone

import pytest

from marketlens.domain.enums import BreakerState, DataMode, DataQuality, ProviderStatus
from marketlens.domain.facts import Fact, build_quality_report, derived
from marketlens.infrastructure.health import HealthRegistry
from marketlens.infrastructure.logging import SecretRedactor
from marketlens.infrastructure.resilience import BreakerConfig, CircuitBreaker, CircuitOpen, RetryConfig, TokenBucket, backoff_delay, retry
from marketlens.providers.contracts import NotSupported, ProviderUnavailable, RateLimited


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_circuit_breaker_transitions():
    c = Clock()
    b = CircuitBreaker("p", BreakerConfig(failure_threshold=2, reset_timeout_s=10), clock=c)

    def boom():
        raise ProviderUnavailable("x")

    for _ in range(2):
        with pytest.raises(ProviderUnavailable):
            b.call(boom)
    assert b.state == BreakerState.OPEN
    with pytest.raises(CircuitOpen):
        b.call(lambda: 1)
    c.t = 11
    assert b.state == BreakerState.HALF_OPEN
    with pytest.raises(ProviderUnavailable):
        b.call(boom)  # failed probe → open again
    assert b.state == BreakerState.OPEN
    c.t = 22
    assert b.call(lambda: 5) == 5 and b.state == BreakerState.CLOSED


def test_not_supported_does_not_trip_breaker():
    b = CircuitBreaker("p", BreakerConfig(failure_threshold=1))

    def ns():
        raise NotSupported("x")

    with pytest.raises(NotSupported):
        b.call(ns)
    assert b.state == BreakerState.CLOSED


def test_retry_with_backoff_and_rate_limit_respect():
    sleeps = []
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RateLimited("429", retry_after=3)
        if calls["n"] == 2:
            raise ProviderUnavailable("down")
        return "ok"

    assert retry(flaky, RetryConfig(attempts=3), sleep=sleeps.append, rng=random.Random(0)) == "ok"
    assert sleeps[0] == 3
    assert 0.5 * 2 * 0.75 <= sleeps[1] <= 0.5 * 2 * 1.25


def test_backoff_grows_and_caps():
    cfg = RetryConfig(base_delay_s=1, max_delay_s=5, jitter=0)
    assert [backoff_delay(i, cfg) for i in range(5)] == [1, 2, 4, 5, 5]


def test_retry_gives_up():
    with pytest.raises(ProviderUnavailable):
        retry(lambda: (_ for _ in ()).throw(ProviderUnavailable("x")), RetryConfig(attempts=2), sleep=lambda _s: None)


def test_token_bucket():
    c = Clock()
    slept = []

    def sleep(s):
        slept.append(s)
        c.t += s

    tb = TokenBucket(2.0, 2, clock=c, sleep=sleep)
    for _ in range(4):
        tb.acquire()
    assert sum(slept) == pytest.approx(1.0)


def test_health_status():
    h = HealthRegistry()
    h.get("p", "price", DataMode.LIVE)
    for _ in range(5):
        h.record("p", True, 10, data_ts=datetime.now(tz=timezone.utc))
    assert h.all()[0].status() == ProviderStatus.HEALTHY
    for _ in range(6):
        h.record("p", False, 10, "err")
    assert h.all()[0].status() == ProviderStatus.DEGRADED
    h.record("p", False, 5, "429", rate_limited_for=60)
    assert h.all()[0].status() == ProviderStatus.RATE_LIMITED
    h.get("q", "news", DataMode.LIVE, configured=False)
    assert next(x for x in h.all() if x.name == "q").status() == ProviderStatus.DOWN
    h.get("s", "macro", DataMode.LIVE)
    h.record("s", True, 1, data_ts=datetime.now(tz=timezone.utc) - timedelta(days=5))
    assert next(x for x in h.all() if x.name == "s").status() == ProviderStatus.STALE


def test_secret_redaction():
    r = SecretRedactor(["supersecretkey123"])
    assert "supersecret" not in r.redact("calling with supersecretkey123")
    assert "abc123" not in r.redact("https://api.x/y?token=abc123&x=1")
    assert "sk-ant-" not in r.redact("key sk-ant-api03-AAAAAAAAAAAAAAAA") or "REDACTED" in r.redact("key sk-ant-api03-AAAAAAAAAAAAAAAA")


def test_data_quality_report():
    rep = build_quality_report({"price": Fact(1.0, "a"), "fundamentals": None, "analyst": Fact(None, "b", quality=DataQuality.CONFLICTING)}, ("price", "fundamentals"))
    assert rep.core_missing == ("fundamentals",) and rep.conflicts == ("analyst",)
    assert rep.overall == DataQuality.MISSING
    assert rep.completeness == pytest.approx(1 / 3)


def test_derived_fact_worst_quality_and_mock_propagates():
    a = Fact(1.0, "x", quality=DataQuality.DELAYED)
    b = Fact(2.0, "y", mode=DataMode.MOCK)
    d = derived(3.0, a, b)
    assert d.quality == DataQuality.DELAYED and d.mode == DataMode.MOCK
    assert derived(None, a).quality == DataQuality.MISSING
