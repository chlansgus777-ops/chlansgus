"""A-grade acceptance: AI committee cost tiers and the material-change gate (no repeated debates)."""

from __future__ import annotations

from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import CommitteeRow
from tests.integration.test_service_api import make_service


def _calls(svc) -> int:
    with svc.sf() as s:
        return sum(len(c.payload.get("calls", [])) for c in s.query(CommitteeRow))


def test_light_review_for_ranks_6_to_20_and_full_committee_for_the_top_5():
    svc = make_service(universe=200)
    svc.run_scan(run_committee=True)
    with svc.sf() as s:
        coms = {c.recommendation_id: c for c in s.query(CommitteeRow)}
        recs = repo.recommendations_for_scan(s, repo.latest_scan(s).id)
    depth_by_rank = {r.rank: coms[r.id].payload.get("depth") for r in recs if r.id in coms and coms[r.id].status != "SKIPPED"}
    assert all(d == "FULL" for k, d in depth_by_rank.items() if k <= 5) and all(d == "LIGHT" for k, d in depth_by_rank.items() if k > 5)
    for c in coms.values():
        n = len([x for x in c.payload.get("calls", []) if x.get("status") != "CACHED"])
        if c.payload.get("depth") == "LIGHT":
            assert n <= 5
        elif c.payload.get("depth") == "FULL" and c.status != "SKIPPED":
            assert n <= 14
    assert _calls(svc) < 20 * 14  # well under "14 calls × top 20"


def test_no_material_change_reuses_the_previous_committee_without_llm_calls():
    svc = make_service(universe=200)
    svc.run_scan(run_committee=True)
    first = _calls(svc)
    svc.data.cache = type(svc.data.cache)()
    svc.run_scan(run_committee=True)  # same moment, same data → nothing material changed
    with svc.sf() as s:
        latest = repo.recommendations_for_scan(s, repo.latest_scan(s).id)
        statuses = {r.committee_status for r in latest if r.rank and r.rank <= 20}
    assert "REUSED" in statuses
    assert _calls(svc) - first < first * 0.2  # at most a small fraction of the first scan's calls
