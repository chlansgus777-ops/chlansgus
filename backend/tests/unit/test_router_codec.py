from dataclasses import dataclass
from datetime import date, datetime, timezone

import pytest

from marketlens.application.codec import decode, encode
from marketlens.domain.enums import DataMode
from marketlens.domain.market import Bar
from marketlens.domain.what_changed import AnalysisDigest, diff, material_reasons
from marketlens.infrastructure.health import HealthRegistry
from marketlens.infrastructure.resilience import RetryConfig
from marketlens.providers.contracts import ProviderUnavailable
from marketlens.providers.router import AllProvidersFailed, ModeMixError, ProviderChain, relative_conflicts


@dataclass
class Q:
    price: float


class P:
    def __init__(self, name, value=None, fail=False, mode=DataMode.LIVE):
        self.name, self.value, self.fail, self.mode = name, value, fail, mode
        self.calls = 0

    def get_quote(self, t):
        self.calls += 1
        if self.fail:
            raise ProviderUnavailable("down")
        return Q(self.value)


def chain(*ps):
    return ProviderChain("price", ps, DataMode.LIVE, HealthRegistry(), retry_cfg=RetryConfig(attempts=1), sleep=lambda _s: None)


def test_failover_to_secondary():
    r = chain(P("a", fail=True), P("b", 10.0)).call("get_quote", "X")
    assert r.provider == "b" and r.value.price == 10.0


def test_all_failed_raises():
    with pytest.raises(AllProvidersFailed):
        chain(P("a", fail=True), P("b", fail=True)).call("get_quote", "X")


def test_conflict_is_reported_not_hidden():
    r = chain(P("a", 100.0), P("b", 110.0)).call("get_quote", "X", cross_check=relative_conflicts(("price",), 0.02))
    assert r.conflicts and "price" in r.conflicts[0]
    ok = chain(P("a", 100.0), P("b", 100.5)).call("get_quote", "X", cross_check=relative_conflicts(("price",), 0.02))
    assert ok.conflicts == []


def test_mock_and_live_never_mixed():
    with pytest.raises(ModeMixError):
        chain(P("live", 1.0), P("mock", 1.0, mode=DataMode.MOCK))


def test_breaker_stops_hammering_failed_provider():
    a = P("a", fail=True)
    ch = chain(a, P("b", 1.0))
    for _ in range(10):
        ch.call("get_quote", "X")
    assert a.calls == 5  # breaker opened after the failure threshold


def test_codec_roundtrip():
    d = AnalysisDigest("X", datetime(2026, 9, 25, tzinfo=timezone.utc), 80.0, {"a": 0.5}, "BUY", 10.0, True, False, 2.0, 0.01, None, date(2026, 8, 1), "g", ("i",), (), "Risk On", 4.2, False, {"macro": "neutral"})
    assert decode(AnalysisDigest, encode(d)) == d
    b = Bar(date(2026, 1, 2), 1, 2, 0.5, 1.5, 100)
    assert decode(Bar, encode(b)) == b
    with pytest.raises(ValueError):
        encode(datetime(2026, 1, 1))


def digest(**kw):
    base = dict(ticker="X", as_of=datetime(2026, 9, 25, tzinfo=timezone.utc), score=76.0, components={"fundamental": 0.7}, action="WATCH", price=100.0,
                in_buy_zone=False, stop_breached=False, rr=1.7, eps_revision_30d=0.01, revenue_revision_30d=0.0, last_earnings_date=date(2026, 8, 1),
                guidance_signature="g1", issue_ids=(), major_issue_ids=(), regime="Risk On", us10y=4.40, thesis_invalidated=False, agent_stances={"macro": "negative"})
    base.update(kw)
    return AnalysisDigest(**base)


def test_what_changed_explains_changes():
    prev = digest()
    cur = digest(score=84.0, action="BUY", in_buy_zone=True, rr=2.5, eps_revision_30d=0.034, us10y=4.27, major_issue_ids=("ISSUE_X",), agent_stances={"macro": "neutral"})
    ch = diff(prev, cur)
    kinds = {c.kind for c in ch}
    assert {"revision", "price_zone", "rr", "score", "issue", "macro", "agent"} <= kinds
    mat = material_reasons(ch)
    assert "price entered the buy zone" in mat and any("R/R" in m for m in mat)


def test_small_price_move_is_not_material():
    ch = diff(digest(), digest(price=101.5, score=76.8))
    assert material_reasons(ch) == ()
