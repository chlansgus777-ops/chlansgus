"""A-grade acceptance: the AI may not change the meaning of a number (actual ↔ forecast)."""

from __future__ import annotations

from datetime import datetime, timezone

from marketlens.application.committee.claims import unverified
from marketlens.application.evidence import EvidenceBuilder

AS_OF = datetime(2026, 9, 25, 20, tzinfo=timezone.utc)


def _ev(**kv):
    eb = EvidenceBuilder("NVDA", AS_OF)
    for k, v in kv.items():
        eb.add(k.replace("__", "."), "x", k, v, "sec")
    return eb.items()


def test_trailing_eps_cannot_support_a_forward_eps_claim():
    """Audit P1: evidence fund.eps_ttm = 5, AI text "NVDA forward EPS is $5." was accepted."""
    ev = _ev(fund__eps_ttm=5.0)
    assert unverified("NVDA forward EPS is $5.", ev, "NVDA", ["NVDA"])
    assert unverified("NVDA의 선행 EPS는 $5입니다.", ev, "NVDA", ["NVDA"])
    assert not unverified("NVDA trailing EPS is $5.", ev, "NVDA", ["NVDA"])
    assert not unverified("NVDA EPS is $5.", ev, "NVDA", ["NVDA"])  # no basis word → any EPS evidence of that value


def test_forecast_evidence_cannot_support_a_reported_claim():
    ev = _ev(analyst__forward_revenue=200e9)
    assert unverified("NVDA reported revenue of $200B.", ev, "NVDA", ["NVDA"])
    assert not unverified("NVDA consensus revenue is $200B.", ev, "NVDA", ["NVDA"])


def test_each_evidence_item_carries_its_basis():
    items = {e.metric: e for e in _ev(fund__eps_ttm=5.0, analyst__forward_eps=6.0, price__current=100.0)}
    assert items["fund.eps_ttm"].basis == "ACTUAL" and items["analyst.forward_eps"].basis == "FORECAST"
