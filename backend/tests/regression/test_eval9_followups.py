"""Follow-ups to the 9th evaluation (H1–H5) beyond the evaluator's own counterexamples: the edges of the split basis
record, holdings entered after a split, the job state when the final write fails, and the guidance forms that must
keep extracting under the rewritten head / tail rules."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

UTC = timezone.utc


# ---------------------------------------------------------------- H1: the basis is the recorded splits, not the date
def test_the_share_basis_follows_the_splits_an_analysis_already_reflected():
    from marketlens.domain.corporate_actions import SplitEvent, factor_since, split_key

    s = SplitEvent("T", date(2026, 9, 24), 1, 10, "polygon")
    today = date(2026, 9, 25)
    assert factor_since([s], (), date(2026, 9, 24), today) == 10.0  # made on the execution day before the sync
    assert factor_since([s], (split_key(s),), date(2026, 9, 24), today) == 1.0  # made after the sync of that day
    assert factor_since([s], (), date(2026, 9, 20), date(2026, 9, 23)) == 1.0  # not executed yet
    assert factor_since([s], None, date(2026, 9, 24), today) == 1.0  # an older record: the date rule (after, through]
    assert factor_since([s], None, date(2026, 9, 23), today) == 10.0


def test_the_digest_records_the_splits_its_bars_reflected():
    from dataclasses import replace

    from marketlens.application.pipeline import run_analysis
    from marketlens.config import load_model_config
    from marketlens.domain.corporate_actions import SplitEvent
    from tests.fixtures import analysis

    _res, inp = analysis("NVDA")
    known = SplitEvent("NVDA", date(2026, 6, 10), 1, 10, "polygon")
    future = SplitEvent("NVDA", date(2027, 1, 5), 1, 2, "polygon")  # not executed at the analysis: not applied
    r = run_analysis(replace(inp, splits=(known, future)), load_model_config())
    assert r.digest.splits_applied == ("2026-06-10:1:10",)


# ---------------------------------------------------------------- H4: a holding entered after the split is left alone
def test_a_holding_entered_after_the_split_is_not_adjusted(tmp_path):
    from marketlens.domain.corporate_actions import SplitEvent
    from marketlens.infrastructure.db import repository as repo
    from marketlens.infrastructure.db.models import HoldingRow

    svc = _live(tmp_path)
    svc.store.save_splits([SplitEvent("NVDA", date(2026, 9, 22), 1, 10, "polygon")])
    with svc.sf() as s:
        repo.upsert_holding(s, "NVDA", 100, 18.0)
        s.get(HoldingRow, "NVDA").updated_at = datetime(2026, 9, 23, 14, tzinfo=UTC)
        s.commit()
        (h,) = svc.portfolio(s).holdings
    assert (h.quantity, h.cost_basis, h.split_adjusted) == (100, 18.0, 1.0)
    with svc.sf() as s:
        s.get(HoldingRow, "NVDA").updated_at = datetime(2026, 9, 1, 14, tzinfo=UTC)
        s.commit()
        (h,) = svc.portfolio(s).holdings
    assert (h.quantity, h.cost_basis, h.split_adjusted) == (1000, pytest.approx(1.8), 10.0)


def _live(tmp_path):  # noqa: ANN001, ANN202
    from marketlens.application.registry import build_live_registry
    from marketlens.application.services import MarketLensService
    from marketlens.config import Settings
    from marketlens.domain.enums import DataMode
    from marketlens.infrastructure.db.models import Base
    from marketlens.infrastructure.db.session import make_engine, make_session_factory
    from marketlens.providers.llm.base import UnavailableLLM
    from tests.live_fixtures import NOW, live_transport

    st = Settings(mode=DataMode.LIVE, database_url="sqlite:///x", llm_provider="none", sec_user_agent="MarketLens test test@example.com",
                  finnhub_api_key="k", fred_api_key="k", polygon_api_key="k", alphavantage_api_key="k")
    reg = build_live_registry(st, transport=live_transport([]), sleep=lambda _s: None)
    eng = make_engine(f"sqlite:///{(tmp_path / 'live.db').as_posix()}")
    Base.metadata.create_all(eng)
    return MarketLensService(st, make_session_factory(eng), registry=reg, llm=UnavailableLLM(), now_fn=lambda: NOW)


# ---------------------------------------------------------------- H5: what the screen shows when the last write failed
def test_the_real_outcome_is_shown_when_the_final_state_could_not_be_saved(tmp_path, monkeypatch):
    import threading

    svc = _live(tmp_path)
    real = svc._sync_state

    def boom(**_k):  # noqa: ANN003, ANN202
        raise RuntimeError("disk full")

    def state(st):  # noqa: ANN001, ANN202
        if st.get("status") != "RUNNING":
            raise RuntimeError("database is locked")
        real(st)

    monkeypatch.setattr(svc, "sync_market", boom)
    monkeypatch.setattr(svc, "_sync_state", state)
    svc.start_sync()
    for t in threading.enumerate():
        if t.name == "marketlens-sync":
            t.join(timeout=60)
    job = svc.sync_status()["job"]
    assert job["status"] == "FAILED" and "disk full" in job["errors"][0] and job.get("note")


def test_a_failing_first_state_write_does_not_keep_the_job_lock(tmp_path, monkeypatch):
    svc = _live(tmp_path)

    def state(_st):  # noqa: ANN001, ANN202
        raise RuntimeError("database is locked")

    monkeypatch.setattr(svc, "_sync_state", state)
    with pytest.raises(RuntimeError):
        svc.start_sync()
    assert svc._sync_lock.acquire(blocking=False)
    svc._sync_lock.release()


# ---------------------------------------------------------------- H2/H3: forms that must keep extracting
@pytest.mark.parametrize("sentence,lo,hi", [
    ("We expect revenue of $5.0 billion to $5.2 billion in fiscal 2027.", 5.0e9, 5.2e9),
    ("We expect revenue of $1.10 billion to $1.15 billion for the fourth quarter.", 1.10e9, 1.15e9),
    ("We expect diluted EPS of $1.10 from continuing operations for fiscal 2027.", 1.10, 1.10),
    ("We expect revenue of $2.0 billion, up 10% year over year, for the fourth quarter.", 2.0e9, 2.0e9),
    ("We expect full-year net sales of $5.1 billion.", 5.1e9, 5.1e9),
    ("We are raising our full-year revenue outlook to $12.0 billion to $12.2 billion.", 12.0e9, 12.2e9),
    ("For the fourth quarter, we expect non-GAAP gross margin of 72% to 73%.", 0.72, 0.73),
    ("Revenue is expected to be $54.0 billion, plus or minus 2%.", 52.92e9, 55.08e9),
    ("We expect GAAP net income of $0.50 per diluted share in the second half of fiscal 2027.", 0.50, 0.50),
])
def test_company_level_guidance_keeps_extracting(sentence, lo, hi):
    from marketlens.domain.guidance import extract

    (i,) = extract(sentence)
    assert i.status == "EXTRACTED" and i.low == pytest.approx(lo) and i.high == pytest.approx(hi), (i.status, i.low, i.high)


@pytest.mark.parametrize("sentence", [
    "We expect licensing revenue of $300 million for the fourth quarter.",  # a part ending in -ing is still a part
    "We expect revenue of $300 million from licensing in fiscal 2027.",
    "Revenue is expected to be $80 million, or 2%, lower than the third quarter.",
])
def test_parts_and_differences_stay_out(sentence):
    from marketlens.domain.guidance import extract

    assert all(i.status != "EXTRACTED" for i in extract(sentence) if i.metric == "revenue")
