"""A verdict made under other decision rules is not carried over by "no material change → keep the previous verdict"
(owner 2026-10-05: MU at 79.5 stayed 관찰 after the buy bar moved 80→68, while ABBV at 73.3 — whose score moved
enough to count as a material change — became 매수)."""

from datetime import timedelta

from sqlalchemy import update

from marketlens.domain.enums import Action
from marketlens.infrastructure.db.models import RecommendationRow
from tests.integration.test_service_api import make_service


def test_a_verdict_of_older_decision_rules_is_not_kept_as_the_previous_one():
    svc = make_service(universe=40)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        row = s.query(RecommendationRow).filter(RecommendationRow.ticker.is_not(None)).first()
        t, as_of, current = row.ticker, row.as_of, row.decision_model_version
    lookup_now = svc._previous_lookup(svc.sf(), current)
    digest, prev = lookup_now(t, as_of + timedelta(seconds=1))
    assert digest is not None and isinstance(prev, Action)  # same rules: the previous verdict is kept for hysteresis
    with svc.sf() as s:
        s.execute(update(RecommendationRow).where(RecommendationRow.ticker == t).values(decision_model_version="decision-3.3.0"))
        s.commit()
    digest, prev = svc._previous_lookup(svc.sf(), current)(t, as_of + timedelta(seconds=1))
    assert digest is not None and prev is None  # made under the old bar: the new rules decide afresh
