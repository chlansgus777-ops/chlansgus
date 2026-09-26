"""A-grade acceptance: issued recommendations are immutable; delisted names keep their outcomes."""

from __future__ import annotations

from datetime import timedelta

from marketlens.application.evaluation_service import EvaluationService
from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import OutcomeRow, SecurityRow
from tests.integration.test_service_api import NOW, make_service


def _snapshot(row):
    return (row.final_action, row.confidence, row.size_class, row.committee_status, row.score, row.input_fingerprint, row.llm_model_ids)


def test_committee_review_creates_a_new_version_and_never_edits_the_issued_one():
    """Audit P1: a later committee run rewrote final_action of the stored recommendation (and paper history)."""
    svc = make_service(universe=80)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        scan = repo.latest_scan(s)
        r1 = repo.recommendations_for_scan(s, scan.id)[0]
        r1_id, before = r1.id, _snapshot(r1)
    out = svc.committee_for_recommendation(r1_id)
    with svc.sf() as s:
        r1 = repo.get_recommendation(s, r1_id)
        assert _snapshot(r1) == before  # the issued version is untouched
        r2 = repo.current_version(s, r1_id)
        assert r2 is not None and r2.id == out["recommendation_id"] != r1_id
        assert r2.supersedes_id == r1_id and r2.version == 2 and r2.as_of == r1.as_of and r2.created_at >= r1.created_at
        assert repo.committee_for(s, r2.id) is not None and repo.committee_for(s, r1_id) is None
        # the scan listing shows the current version once; history keeps both
        listed = [r.id for r in repo.recommendations_for_scan(s, scan.id) if r.ticker == r1.ticker]
        assert listed == [r2.id]
        assert repo.original_version(s, r2.id).id == r1_id
    again = svc.committee_for_recommendation(r1_id)  # idempotent: the stored committee is returned
    assert again["recommendation_id"] == r2.id


def test_delisted_name_keeps_its_outcome_even_when_missing_from_the_current_universe():
    """Audit P1: delisting was read from the current scan context, so a vanished name stayed pending."""
    svc = make_service(universe=80)
    svc._clock["t"] = NOW - timedelta(weeks=8)
    svc.registry.world.set_now(svc._clock["t"])
    svc.run_scan(run_committee=False)
    svc._clock["t"] = NOW
    svc.registry.world.set_now(NOW)
    svc.data.cache = type(svc.data.cache)()
    with svc.sf() as s:
        rec = repo.all_recommendations(s)[0]
        ticker, rec_id = rec.ticker, rec.id
        row = s.get(SecurityRow, ticker)
        row.active, row.delisted_at = False, (rec.as_of + timedelta(days=10)).date()
        s.commit()
    svc.last_scan_context = None  # the current universe no longer knows the name
    EvaluationService(svc).update_outcomes()
    with svc.sf() as s:
        statuses = {o.status for o in s.query(OutcomeRow).filter(OutcomeRow.recommendation_id == rec_id)}
    assert "DELISTED_LAST_PRICE" in statuses
