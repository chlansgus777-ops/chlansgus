"""9차 독립 평가 (대상 커밋 2b217e2) — 새 반례. 원본: evaluations/eval9 (평가자 작성, 수정 없이 옮김).

각 테스트는 입력과 기대 동작을 그대로 적었다. 2b217e2 에서는 모두 실패하고, 고친 뒤에는 통과해야 한다.
실행: 저장소 루트에서  python -m pytest -q -o addopts="" evaluations/eval9
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

UTC = timezone.utc


def _live(tmp_path):
    """A LIVE service on a file SQLite store, the real provider code on HTTP fixtures (tests.live_fixtures), clock at
    Friday 2026-09-25 11:00 New York. The same set-up as backend/tests/integration/test_background_sync.py."""
    from marketlens.application.registry import build_live_registry
    from marketlens.application.services import MarketLensService
    from marketlens.config import Settings
    from marketlens.domain.enums import DataMode
    from marketlens.infrastructure.db.models import Base
    from marketlens.infrastructure.db.session import make_engine, make_session_factory
    from marketlens.providers.llm.base import UnavailableLLM
    from tests.live_fixtures import NOW, live_transport

    keys = {"sec_user_agent": "MarketLens test test@example.com", "finnhub_api_key": "fixture-key", "fred_api_key": "fixture-key",
            "polygon_api_key": "fixture-key", "alphavantage_api_key": "fixture-key"}
    st = Settings(mode=DataMode.LIVE, database_url="sqlite:///x", llm_provider="none", **keys)
    reg = build_live_registry(st, transport=live_transport([]), sleep=lambda _s: None)
    eng = make_engine(f"sqlite:///{(tmp_path / 'live.db').as_posix()}")
    Base.metadata.create_all(eng)
    return MarketLensService(st, make_session_factory(eng), registry=reg, llm=UnavailableLLM(), now_fn=lambda: NOW)


def _join_sync_job() -> None:
    for t in threading.enumerate():
        if t.name == "marketlens-sync":
            t.join(timeout=120)
            assert not t.is_alive()


# ============================================================================ H1 (P1) the split's own execution day
# I1 was fixed for a previous analysis made on an EARLIER day: its levels are divided by the splits executed in
# (previous day, today]. The levels of an analysis are on the basis of the bars it used, not of its date: an analysis
# run on the execution day before that day's sync (the store fetches only splits that have executed, and rescales the
# bars when it saves them) still has pre-split levels, and (D, today] leaves the split out.
def _analysis_after_a_split_day_scan(previous_action: str, carried: bool):
    """NVDA from the mock market (tests.fixtures). The split executed on Thursday D = 2026-09-24. The previous analysis
    ran on D at 08:00 New York, before the day's sync — the store had no split yet, so it computed its stop from the
    unadjusted bars: exactly the pre-split levels of the fixture analysis (stop 350.26, price 366.44). The sync after
    the close saved the split and rescaled the stored bars. Now (Friday) every bar and the quote are on the post-split
    basis; the stock itself did not fall. The position is held."""
    from marketlens.application.pipeline import run_analysis
    from marketlens.config import load_model_config
    from marketlens.domain.corporate_actions import SplitEvent
    from marketlens.domain.decision import Action
    from marketlens.domain.market import Bar
    from marketlens.domain.market_calendar import NY
    from tests.fixtures import analysis

    res, inp = analysis("NVDA")
    d = inp.bars[-1].day  # 2026-09-24
    stop = res.entry.stop
    prev = replace(res.digest, as_of=datetime.combine(d, time(8, 0), tzinfo=NY), action=previous_action, stop=stop,
                   guard_stop=stop if carried else None)
    k = 10.0
    bars = tuple(Bar(b.day, b.open / k, b.high / k, b.low / k, b.close / k, b.volume * k) for b in inp.bars)
    kw = dict(previous=prev, previous_action=Action(previous_action), held=True, bars=bars,
              quote=replace(inp.quote, price=inp.quote.price / k), splits=(SplitEvent("NVDA", d, 1, 10, "polygon"),))
    if inp.analyst is not None and inp.analyst.forward_eps:
        kw["analyst"] = replace(inp.analyst, forward_eps=inp.analyst.forward_eps / k)
    return run_analysis(replace(inp, **kw), load_model_config())


@pytest.mark.parametrize("previous_action,carried", [("BUY", False), ("HOLD", True)], ids=["previous-BUY", "HOLD-with-carried-stop"])
def test_H1_an_analysis_made_on_the_execution_day_before_the_sync_keeps_its_pre_split_stop(previous_action, carried):
    """2b217e2: close 37.58 (post-split) ≤ stop 350.26 (pre-split) → "종가 기준 이탈: … → 매도" (SELL). The same previous
    analysis dated one day earlier gives HOLD. With the scheduler on, every split day is this case: it scans from the
    pre-market on and syncs only after the close."""
    r = _analysis_after_a_split_day_scan(previous_action, carried)
    stop_reasons = [x for x in r.decision.reasons if "종가 기준 이탈" in x]
    assert not stop_reasons and r.decision.action.value != "SELL", (r.decision.action, stop_reasons)


def test_H1_a_stored_recommendation_from_the_execution_day_is_shown_on_todays_basis(tmp_path):
    """levels_now() uses the same (analysis day, today] window: a BUY stored on the execution day before the sync keeps
    its pre-split stop and max buy next to post-split quotes (the listing's plan check, the stock screen, the dollar
    amount). Its stored inputs say which splits it knew: none."""
    from marketlens.domain.corporate_actions import SplitEvent
    from marketlens.domain.market_calendar import NY
    from marketlens.infrastructure.db.models import RecommendationRow

    svc = _live(tmp_path)
    d = date(2026, 9, 24)
    svc.store.save_splits([SplitEvent("NVDA", d, 1, 10, "polygon")])
    row = RecommendationRow(ticker="NVDA", as_of=datetime.combine(d, time(8, 0), tzinfo=NY), price=366.44, final_action="BUY",
                            result={"entry": {"ideal_entry": 360.0, "max_buy": 372.0, "stop": 350.26, "target1": 400.0}},
                            inputs={"splits": []})
    lv = svc.levels_now(row)
    assert lv["stop"] == pytest.approx(35.026) and lv["max_buy"] == pytest.approx(37.2), lv


# ============================================================================ H2 (P2) guidance: amounts that are not the company's level, still EXTRACTED
# (a) the part is named AFTER the amount ("revenue of $500 million from the acquired business")
PART_AFTER_THE_AMOUNT = [
    ("revenue", "We expect revenue of approximately $500 million from the acquired business in fiscal 2027."),
    ("revenue", "For the fourth quarter, we expect revenue of $1.2 billion from our data center products."),
    ("revenue", "Revenue is expected to be approximately $30 billion in the Data Center segment for the third quarter of fiscal 2027."),
    ("revenue", "We expect revenue of $1.2 billion in the Americas for the fourth quarter."),
    ("eps", "We expect adjusted EPS of $0.25 from the acquired business in fiscal 2027."),
]
# (b) a segment in front of an allowed word ("net", "adjusted", "GAAP"): only the one word before the metric is checked
PART_BEFORE_AN_ALLOWED_WORD = [
    ("revenue", "Data center net revenue is expected to be $30 billion for the third quarter of fiscal 2027."),
    ("operating_margin", "The Company expects Cloud segment adjusted operating margin of approximately 21% for fiscal 2027."),
    ("gross_margin", "We expect Services GAAP gross margin of 75% for the fourth quarter."),
]
# (c) a difference whose comparative word does not follow the amount directly (", or 5%,", "or 3%", "a share")
DIFFERENCE_WITH_A_GAP = [
    ("revenue", "Revenue for the fourth quarter is expected to be $50 million, or 5%, higher than the prior year."),
    ("revenue", "Full-year revenue is expected to be $120 million, or 4 percent, lower than fiscal 2026."),
    ("revenue", "Revenue for fiscal 2027 is expected to be $200 million or 3% above fiscal 2026."),
    ("eps", "Adjusted EPS for fiscal 2027 is expected to be $0.15 per share, or 6%, above fiscal 2026."),
    ("eps", "Adjusted EPS is expected to be $0.10 a share higher than last year."),
]


@pytest.mark.parametrize("metric,sentence", PART_AFTER_THE_AMOUNT + PART_BEFORE_AN_ALLOWED_WORD + DIFFERENCE_WITH_A_GAP)
def test_H2_an_amount_that_is_not_the_companys_level_is_never_extracted(metric, sentence):
    from marketlens.domain.guidance import extract

    items = [i for i in extract(sentence) if i.metric == metric]
    assert all(i.status != "EXTRACTED" for i in items), [(i.low, i.high, i.status, i.period_label) for i in items]


def _guide_vs_consensus(tmp_path, text: str):
    """Total revenue consensus for the next quarter 54.0 billion; a quarter that beat (as in the 8th evaluation's I2)."""
    from marketlens.application.estimate_book import attach_guidance
    from marketlens.application.market_store import MarketStore
    from marketlens.domain.earnings import EarningsReport, assess_earnings
    from marketlens.domain.estimates import EstimateObservation
    from marketlens.domain.guidance import extract
    from marketlens.infrastructure.db.models import Base
    from marketlens.infrastructure.db.session import make_engine, make_session_factory

    eng = make_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    Base.metadata.create_all(eng)
    st = MarketStore(make_session_factory(eng), "LIVE")
    st.save_guidance("ABC", "0000-26-000041", datetime(2026, 11, 5, 21, tzinfo=UTC), "https://www.sec.gov/x", extract(text))
    st.save_estimates([EstimateObservation("ABC", "finnhub", "2027Q1", "quarter", None, date(2026, 11, 1), eps=1.47, revenue=54.0e9,
                                           report_date=date(2027, 2, 5))])
    reports = [EarningsReport(date(2026, 11, 5), "Q3 FY2026", "finnhub", revenue_actual=51.0e9, revenue_consensus=50.0e9, eps_actual=1.52, eps_consensus=1.40)]
    rep = attach_guidance(reports, st.guidance("ABC", datetime(2026, 11, 6, tzinfo=UTC)), st.estimate_history("ABC", date(2026, 11, 6)), date(2026, 11, 6))
    return assess_earnings(rep)


@pytest.mark.parametrize("text", [
    "Fourth quarter outlook. We expect revenue of approximately $40.0 billion from data center products. "
    "Total revenue is expected to be $54.0 billion, plus or minus 2%.",
    "Fourth quarter outlook. Revenue is expected to be $2.0 billion, or 4%, higher than the prior-year quarter.",
], ids=["part-line-before-the-total", "difference-only"])
def test_H2_end_to_end_a_part_or_a_difference_is_not_the_next_quarters_revenue_guidance(tmp_path, text):
    """2b217e2: −26% (the data-center line is taken as the guide) and −96% (the difference is taken as the level):
    both BEAT_WEAK_GUIDE for a quarter that beat and guided in line."""
    a = _guide_vs_consensus(tmp_path, text)
    assert a.guide_rev_vs_cons is None or abs(a.guide_rev_vs_cons) < 0.05, (a.guide_rev_vs_cons, a.result_quality)


# ============================================================================ H3 (P3) guidance: common company-wide forms now missed
# Every sentence below was EXTRACTED with these values at 1b48217 and is GUIDANCE_UNCLEAR at 2b217e2: the word right
# before the metric is not in the allow-list (a verb such as "report"/"projecting", a possessive, "annual", "with").
COMPANY_WIDE = [
    ("revenue", "The Company is forecasting revenue of $2.10 billion to $2.20 billion for fiscal 2027.", 2.10e9, 2.20e9),
    ("revenue", "We are projecting revenue of $5.0 billion to $5.2 billion for fiscal 2027.", 5.0e9, 5.2e9),
    ("revenue", "The Company expects to report revenue of $1.10 billion to $1.15 billion for the fourth quarter.", 1.10e9, 1.15e9),
    ("revenue", "We expect to generate revenue of $5.0 billion to $5.2 billion in fiscal 2027.", 5.0e9, 5.2e9),
    ("revenue", "The Company's revenue for the fourth quarter is expected to be $1.10 billion to $1.15 billion.", 1.10e9, 1.15e9),
    ("revenue", "We expect annual revenue of $5.0 billion to $5.2 billion for fiscal 2027.", 5.0e9, 5.2e9),
    ("revenue", "For the fourth quarter, we expect a record quarter, with revenue of $1.10 billion to $1.15 billion.", 1.10e9, 1.15e9),
    ("revenue", "The Company now expects this year's revenue to be $5.0 billion to $5.2 billion.", 5.0e9, 5.2e9),
    ("revenue", "We are updating revenue guidance to $5.0 billion to $5.2 billion for fiscal 2027.", 5.0e9, 5.2e9),
    ("revenue", "We are maintaining revenue guidance of $5.0 billion to $5.2 billion for fiscal 2027.", 5.0e9, 5.2e9),
    ("revenue", "The Company expects to deliver revenue of $5.0 billion to $5.2 billion in fiscal 2027.", 5.0e9, 5.2e9),
    ("eps", "The Company expects to report EPS of $1.10 to $1.20 for the fourth quarter.", 1.10, 1.20),
    ("eps", "The Company's EPS for fiscal 2027 is expected to be $4.10 to $4.30.", 4.10, 4.30),
    ("eps", "We are projecting EPS of $4.10 to $4.30 for fiscal 2027.", 4.10, 4.30),
    ("eps", "We expect annual EPS of $4.10 to $4.30 for fiscal 2027.", 4.10, 4.30),
    ("gross_margin", "The Company's gross margin for the fourth quarter is expected to be 45% to 46%.", 0.45, 0.46),
    ("gross_margin", "We are projecting gross margin of 45% to 46% for the fourth quarter.", 0.45, 0.46),
]


@pytest.mark.parametrize("metric,sentence,lo,hi", COMPANY_WIDE)
def test_H3_common_company_wide_guidance_is_still_extracted(metric, sentence, lo, hi):
    from marketlens.domain.guidance import extract

    items = [i for i in extract(sentence) if i.metric == metric]
    assert any(i.status == "EXTRACTED" and i.low == pytest.approx(lo) and i.high == pytest.approx(hi) for i in items), \
        [(i.status, i.low, i.high) for i in items]


# ============================================================================ H4 (P2) holdings entered before a split
def _holding_across_a_split(tmp_path):
    """The user entered 10 NVDA at 180 on 1 September (cash 10,000). A 10-for-1 split executed on 22 September; the
    store holds every bar on the post-split basis (18), as after adjust_bars_for_splits. The holding's row keeps the
    date it was entered (updated_at), so the app knows its quantity and cost are pre-split."""
    from marketlens.domain.corporate_actions import SplitEvent
    from marketlens.domain.market import Bar
    from marketlens.domain.market_calendar import is_trading_day
    from marketlens.infrastructure.db import repository as repo
    from marketlens.infrastructure.db.models import HoldingRow

    svc = _live(tmp_path)
    days = [d for d in (date(2026, 6, 1) + timedelta(days=i) for i in range(120)) if is_trading_day(d) and d <= date(2026, 9, 24)]
    svc.store.save_bars("NVDA", [Bar(d, 18.0, 18.2, 17.8, 18.0, 1e8) for d in days], "polygon")
    svc.store.save_bars("SPY", [Bar(d, 650.0, 652.0, 648.0, 650.0, 1e8) for d in days], "polygon")
    svc.store.save_splits([SplitEvent("NVDA", date(2026, 9, 22), 1, 10, "polygon")])
    with svc.sf() as s:
        repo.set_setting(s, "portfolio_cash", "10000")
        repo.upsert_holding(s, "NVDA", 10, 180.0)
        s.get(HoldingRow, "NVDA").updated_at = datetime(2026, 9, 1, 14, tzinfo=UTC)
        s.commit()
    return svc


def test_H4_a_holding_entered_before_a_split_is_valued_on_todays_share_basis(tmp_path):
    """2b217e2: quantity 10 × 18 = 180 (it is 100 shares, 1,800), unrealized −90%, weight 1.8% (really 15%)."""
    from fastapi.testclient import TestClient

    from marketlens.api.app import create_app

    svc = _holding_across_a_split(tmp_path)
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app, headers={"X-MarketLens-Client": "test"}) as c:
        h = c.get("/api/portfolio").json()["holdings"][0]
    assert h["market_value"] == pytest.approx(1800.0) and abs(h["unrealized_pct"]) < 0.01, h


def test_H4_an_add_is_not_sized_past_the_single_name_limit_after_a_split(tmp_path):
    """The position is really 1,800 of 11,800 (15%) — over the 10% single-name limit, so an ADD must buy nothing
    (8th evaluation I6: "이미 한 종목 한도 …에 도달"). 2b217e2 sizes it as 180 of 10,180 and proposes 14 more shares."""
    from marketlens.api.routes import _position_plan

    svc = _holding_across_a_split(tmp_path)
    row = SimpleNamespace(ticker="NVDA", final_action="ADD", result={"decision": {"size_limit": None}})
    with svc.sf() as ss:
        p = _position_plan(svc, ss, row, {"price": 18.0, "stop": 16.5})
    assert not p.get("shares"), p


# ============================================================================ H5 (P3) background data preparation: concurrency and error handling
def test_H5_a_sync_started_elsewhere_while_the_job_runs_does_not_download_the_same_sessions_again(tmp_path, monkeypatch):
    """sync_market() — also behind POST /api/sync, `marketlens sync` and the scheduler's after-close step — does not
    look at the job's lock. While the job waits on its first grouped-daily answer, another sync downloads the same
    sessions; the job then downloads them all again from its own list (the application: "nothing is fetched twice").
    Days the provider answers empty (pending or outside its window) are not counted."""
    from marketlens.application.sync import _find

    svc = _live(tmp_path)
    grouped = _find(svc.registry, "price", "get_grouped_daily")
    real = grouped.get_grouped_daily
    loaded: Counter = Counter()
    job_waiting, go_on = threading.Event(), threading.Event()

    def spy(d):  # noqa: ANN001, ANN202
        if threading.current_thread().name == "marketlens-sync" and not go_on.is_set():
            job_waiting.set()
            go_on.wait(timeout=20)
        bars = real(d)
        if bars:
            loaded[d] += 1
        return bars

    monkeypatch.setattr(grouped, "get_grouped_daily", spy)
    assert svc.start_sync()["started"] is True
    assert job_waiting.wait(timeout=20)
    other = threading.Thread(target=svc.sync_market, name="another-sync")
    other.start()
    other.join(timeout=5)  # a guarded implementation may refuse or wait for the job; give it a moment either way
    go_on.set()
    other.join(timeout=120)
    _join_sync_job()
    twice = sorted(d for d, n in loaded.items() if n > 1)
    assert not twice, f"{len(twice)} sessions downloaded twice, e.g. {twice[:3]}"


def test_H5_the_job_frees_itself_when_its_last_state_cannot_be_written(tmp_path, monkeypatch):
    """A round fails on a busy database ("database is locked"); writing the final FAILED state right after fails the
    same way. The exception leaves the `finally` before `_sync_lock.release()`: the lock is held until the app restarts,
    GET /sync/status keeps reading RUNNING, and the screen shows "데이터 받는 중…" with the button disabled."""
    from sqlalchemy.exc import OperationalError

    svc = _live(tmp_path)
    busy = OperationalError("UPDATE settings SET value=?", {}, Exception("database is locked"))
    real_state = svc._sync_state

    def round_fails(**_k):  # noqa: ANN003, ANN202
        raise busy

    def state(st):  # noqa: ANN001, ANN202
        if st.get("status") != "RUNNING":
            raise busy
        real_state(st)

    monkeypatch.setattr(svc, "sync_market", round_fails)
    monkeypatch.setattr(svc, "_sync_state", state)
    assert svc.start_sync()["started"] is True
    _join_sync_job()
    monkeypatch.setattr(svc, "_sync_state", real_state)  # the database is free again
    job = svc.sync_status()["job"]
    assert job["status"] != "RUNNING", job
    assert svc.start_sync()["started"] is True, "the button must work again without restarting the app"
    _join_sync_job()
