"""A minute-old analysis is not "만료" because a side field (news, estimates…) is stale or two sources disagree on it —
only its core fields (price, price history, financials) decide whether it can be acted on (owner 2026-10-05, MU:
"매수 · 만료 · 충돌"). A stale / conflicting / missing core field still does (and the decision vetoes it already)."""

from datetime import datetime, timedelta, timezone

from marketlens.domain.enums import DataQuality
from marketlens.domain.facts import DataQualityReport, execution_quality
from marketlens.domain.freshness import recommendation_freshness

F, S, C, M, D = DataQuality.FRESH, DataQuality.STALE, DataQuality.CONFLICTING, DataQuality.MISSING, DataQuality.DELAYED


def rep(**q):
    return DataQualityReport(fields=tuple(q.items()), core_missing=tuple(k for k, v in q.items() if k in ("price", "price_history", "fundamentals") and v in (M, C)),
                             conflicts=tuple(k for k, v in q.items() if v == C), stale=tuple(k for k, v in q.items() if v == S))


def test_only_core_fields_decide_whether_an_analysis_can_be_acted_on():
    side_conflict = rep(price=F, price_history=F, fundamentals=F, estimates=C, news=S)
    assert side_conflict.overall == C  # the recorded overall quality still says so (shown as a badge)
    assert execution_quality(side_conflict, "CONFLICTING") == "FRESH"
    assert execution_quality(rep(price=D, price_history=F, fundamentals=F), "DELAYED") == "DELAYED"
    assert execution_quality(rep(price=F, price_history=S, fundamentals=F), "STALE") == "STALE"
    assert execution_quality(rep(price=F, price_history=F, fundamentals=M), "MISSING") == "MISSING"
    # the stored JSON form of the report, and no report at all (older rows): the recorded value unchanged
    stored = {"fields": [["price", "FRESH"], ["price_history", "FRESH"], ["fundamentals", "FRESH"], ["estimates", "CONFLICTING"]], "core_missing": [], "conflicts": ["estimates"], "stale": []}
    assert execution_quality(stored, "CONFLICTING") == "FRESH"
    assert execution_quality(None, "CONFLICTING") == "CONFLICTING"


def test_a_fresh_buy_with_a_side_conflict_is_current_not_expired():
    made = datetime(2026, 10, 5, 14, 10, tzinfo=timezone.utc)
    q = execution_quality(rep(price=F, price_history=F, fundamentals=F, estimates=C), "CONFLICTING")
    assert recommendation_freshness(made, q, made + timedelta(minutes=1)).status == "CURRENT"
    assert recommendation_freshness(made, "CONFLICTING", made + timedelta(minutes=1)).status == "EXPIRED"  # what it said before
