"""Invariant: nothing published after the analysis time is used. On a LIVE store filled by the real provider code (HTTP
fixtures), the same analysis is run at a pre-market, an intraday and an after-hours time, then again after records
published LATER have been stored — a bar of a later session, an 8-K outlook accepted five minutes after the analysis,
a consensus snapshot observed the next day, a 10-Q filed the next day. The analysis inputs (their fingerprint), the
score and the decision must not change."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from marketlens.domain.market_calendar import NY

UTC = timezone.utc
TIMES = {
    "pre_market": datetime(2026, 9, 25, 8, 0, tzinfo=NY),
    "intraday": datetime(2026, 9, 25, 11, 0, tzinfo=NY),
    "after_hours": datetime(2026, 9, 25, 17, 0, tzinfo=NY),
}


def _world(tmp_path, when: datetime):  # noqa: ANN001, ANN202
    from marketlens.application.registry import build_live_registry
    from marketlens.application.services import MarketLensService
    from marketlens.config import Settings
    from marketlens.domain.enums import DataMode
    from marketlens.infrastructure.db.models import Base
    from marketlens.infrastructure.db.session import make_engine, make_session_factory
    from marketlens.providers.llm.base import UnavailableLLM
    from tests.live_fixtures import live_transport

    st = Settings(mode=DataMode.LIVE, database_url="sqlite:///x", llm_provider="none", sec_user_agent="MarketLens test test@example.com",
                  finnhub_api_key="k", fred_api_key="k", polygon_api_key="k", alphavantage_api_key="k")
    reg = build_live_registry(st, transport=live_transport([]), sleep=lambda _s: None)
    eng = make_engine(f"sqlite:///{(tmp_path / 'pit.db').as_posix()}")
    Base.metadata.create_all(eng)
    clock = {"t": when.astimezone(UTC)}
    svc = MarketLensService(st, make_session_factory(eng), registry=reg, llm=UnavailableLLM(), now_fn=lambda: clock["t"])
    svc.sync_market(max_bar_calls=250, max_profiles=10)
    return svc


def _analysis(svc):  # noqa: ANN001, ANN202
    r, _c, _id = svc.analyze("NVDA", run_committee=False, persist=False)
    return r


def _publish_later(svc, when: datetime) -> None:  # noqa: ANN001
    from marketlens.domain.estimates import EstimateObservation
    from marketlens.domain.guidance import GuidanceItem
    from marketlens.domain.market import Bar

    st = svc.store
    nxt = when.astimezone(NY).date() + timedelta(days=3)  # a later session
    st.save_grouped(nxt, {"NVDA": Bar(nxt, 1.0, 1.0, 1.0, 1.0, 1e9)}, "polygon")  # a crash nobody could have known
    later = when.astimezone(UTC) + timedelta(minutes=5)
    st.save_guidance("NVDA", "0000000000-26-999999", later, "https://www.sec.gov/later",
                     [GuidanceItem("revenue", 1.0e9, 1.1e9, "USD", "fourth quarter", "We expect revenue of $1.0 billion to $1.1 billion.", "EXTRACTED", "MEDIUM")])
    st.save_estimates([EstimateObservation("NVDA", "alphavantage", "annual:2027-01-31", "annual", None, when.astimezone(NY).date() + timedelta(days=1),
                                           eps=0.01, analyst_count=1)])
    quarters = st.quarters("NVDA", None) or []
    if quarters:
        q = quarters[-1]
        st.save_quarters("NVDA", [replace(q, period_end=q.period_end + timedelta(days=91), filed_date=when.astimezone(NY).date() + timedelta(days=1),
                                          revenue=(q.revenue or 1.0) * 0.1, field_filed={})])


@pytest.mark.parametrize("session", list(TIMES))
def test_later_records_change_nothing(tmp_path, session):
    when = TIMES[session]
    svc = _world(tmp_path, when)
    before = _analysis(svc)
    _publish_later(svc, when)
    after = _analysis(svc)
    assert after.input_fingerprint == before.input_fingerprint, session
    assert after.scorecard.total == before.scorecard.total and after.decision.action == before.decision.action
