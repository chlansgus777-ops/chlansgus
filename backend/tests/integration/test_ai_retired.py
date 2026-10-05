"""The AI committee is retired from the app (owner 2026-10-05: "ai위원회기능 쓰지도않는데 없애버려").

The app's settings turn it off; with it off a scan makes no LLM call and stores no committee, an AI hold or size limit
from before the removal is not carried into new analyses or the live verdict, and the manual route refuses. The engine
module itself stays and keeps its own safety tests (tests/ai_safety, the acceptance suites run it switched on)."""

from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from marketlens.api.app import create_app
from marketlens.config import load_settings
from marketlens.infrastructure.db import repository as repo
from marketlens.infrastructure.db.models import CommitteeRow, RecommendationRow
from marketlens.providers.llm.mock_llm import MockLLMProvider
from tests.integration.test_service_api import make_service


class _Counting(MockLLMProvider):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def complete_json(self, *a, **k):  # type: ignore[no-untyped-def]
        self.calls += 1
        return super().complete_json(*a, **k)


def test_the_app_settings_have_the_committee_off(monkeypatch):
    monkeypatch.delenv("ENABLE_AI_COMMITTEE", raising=False)
    monkeypatch.setattr("marketlens.config._load_dotenv", lambda: None)
    assert load_settings().enable_ai_committee is False


def _retired(universe=40):
    llm = _Counting()
    svc = make_service(llm=llm, universe=universe)
    svc.settings = replace(svc.settings, enable_ai_committee=False)
    return svc, llm


def test_a_scan_makes_no_ai_call_and_stores_no_committee():
    svc, llm = _retired()
    svc.run_scan(run_committee=True)  # even when asked
    assert llm.calls == 0
    with svc.sf() as s:
        assert s.scalar(select(func.count()).select_from(CommitteeRow)) == 0
        rows = s.scalars(select(RecommendationRow)).all()
        assert rows and {r.committee_status for r in rows} == {"NOT_RUN"}
        assert all(r.final_action == r.deterministic_action for r in rows)


def test_an_old_ai_hold_is_not_carried_into_a_new_analysis():
    svc, _ = _retired()
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        row = next(r for r in repo.recommendations_for_scan(s, repo.latest_scan(s).id) if r.deterministic_action in ("BUY", "BUY SMALL"))
        t = row.ticker
        # a recommendation from before the removal that the committee had lowered to WATCH
        row.committee_status, row.final_action, row.size_class = "COMPLETED", "WATCH", "WATCH"
        s.commit()
    r, _c, rec_id = svc.analyze(t, run_committee=False, persist=True)
    with svc.sf() as s:
        new = repo.get_recommendation(s, rec_id)
        assert new.committee_status == "NOT_RUN"
        assert new.final_action == r.decision.action.value  # the rules' own verdict, no AI hold carried on


def test_the_manual_ai_route_refuses():
    svc, llm = _retired()
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        rec_id = repo.recommendations_for_scan(s, repo.latest_scan(s).id)[0].id
    with TestClient(create_app(svc.settings, service=svc, run_migrations=False), headers={"X-MarketLens-Client": "t"}) as c:
        res = c.post(f"/api/recommendations/{rec_id}/committee")
    assert res.status_code == 409 and "제거" in res.json()["detail"]
    assert llm.calls == 0
