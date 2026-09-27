"""8차 독립 평가 (대상 커밋 1b48217) — 새 반례. 원본: evaluations/eval8 (평가자 작성, 수정 없이 옮김).

각 테스트는 입력과 기대 동작을 그대로 적었다. 1b48217 에서는 모두 실패하고, 고친 뒤에는 통과해야 한다.
실행: 저장소 루트에서  python -m pytest -q -o addopts="" evaluations/eval8
"""

from __future__ import annotations

import os
import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

from marketlens.application.market_store import MarketStore
from marketlens.infrastructure.db.models import Base
from marketlens.infrastructure.db.session import make_engine, make_session_factory

UTC = timezone.utc


def _store(tmp_path) -> MarketStore:
    eng = make_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    Base.metadata.create_all(eng)
    return MarketStore(make_session_factory(eng), "LIVE")


# ============================================================================ I1 (P1) a stop set before a split is compared with post-split bars
def _after_a_split(previous_action: str, carried: bool):
    """NVDA from the mock market (tests.fixtures): the previous analysis, five days ago, set a stop just below the
    price. A 10-for-1 split executed two days ago. As in the real store, the bars (and the quote) are now on the
    post-split basis — the stock itself did not fall. The position is held."""
    from dataclasses import replace

    from marketlens.application.pipeline import run_analysis
    from marketlens.config import load_model_config
    from marketlens.domain.corporate_actions import SplitEvent
    from marketlens.domain.decision import Action
    from marketlens.domain.market import Bar
    from tests.conftest import NOW
    from tests.fixtures import analysis

    res, inp = analysis("NVDA")
    stop = res.entry.stop  # 350.26 on the pre-split basis (price 366.44)
    prev = replace(res.digest, as_of=NOW - timedelta(days=5), action=previous_action, stop=stop, guard_stop=stop if carried else None)
    k = 10.0
    bars = tuple(Bar(b.day, b.open / k, b.high / k, b.low / k, b.close / k, b.volume * k) for b in inp.bars)
    kw = dict(previous=prev, previous_action=Action(previous_action), held=True, bars=bars,
              quote=replace(inp.quote, price=inp.quote.price / k), splits=(SplitEvent("NVDA", (NOW - timedelta(days=2)).date(), 1, 10, "polygon"),))
    if inp.analyst is not None and inp.analyst.forward_eps:
        kw["analyst"] = replace(inp.analyst, forward_eps=inp.analyst.forward_eps / k)
    return run_analysis(replace(inp, **kw), load_model_config())


@pytest.mark.parametrize("previous_action,carried", [("BUY", False), ("HOLD", True)], ids=["previous-BUY", "HOLD-with-carried-stop"])
def test_I1_a_split_is_not_a_stop_breach(previous_action, carried):
    """1b48217: close 36.64 (post-split) ≤ stop 350.26 (pre-split) → "종가 기준 이탈: … → 매도" (SELL). Without the split the
    same inputs give HOLD. The BUY case existed before; the carried stop (guard_stop, new in this round) keeps a
    pre-split stop for the whole holding period, so every split of a held name now ends in a false SELL."""
    r = _after_a_split(previous_action, carried)
    stop_reasons = [x for x in r.decision.reasons if "종가 기준 이탈" in x]
    assert not stop_reasons and r.decision.action.value != "SELL", (r.decision.action, stop_reasons)


# ============================================================================ I2 (P2) the structural guidance rule: two shapes it still accepts
# (a) the amount is a change because of what comes AFTER it ("… $0.10 higher than last year")
CHANGE_AFTER_THE_AMOUNT = [
    ("eps", "We expect fourth quarter EPS to be $0.10 higher than the prior-year quarter."),
    ("eps", "We expect fourth quarter EPS to be $0.05 to $0.08 lower than last year."),
    ("eps", "We expect fiscal 2027 EPS of $0.40 more than fiscal 2026."),
    ("revenue", "We expect fiscal 2027 revenue to be $2.0 billion higher than fiscal 2026."),
    ("revenue", "We expect fourth quarter revenue to be $50 million to $60 million above the third quarter."),
    ("revenue", "We expect fourth quarter revenue to be approximately $150 million below the prior year."),
    ("gross_margin", "We expect gross margin for the fourth quarter to be 1.5% lower than the prior year."),
]
# (b) a segment or a component named in front of the metric word is not the company's metric
PART_OF_THE_METRIC = [
    ("revenue", "We expect data center revenue of approximately $40.0 billion in the fourth quarter."),
    ("revenue", "We expect subscription revenue of $1.20 billion to $1.25 billion for the fourth quarter."),
    ("revenue", "For the fourth quarter, we expect services revenue of approximately $300 million."),
    ("eps", "We expect interest income of approximately $0.05 per share in the fourth quarter."),
    ("eps", "We expect equity earnings of $0.05 to $0.07 per share in the fourth quarter."),
    ("gross_margin", "We expect services gross margin of approximately 70% in the fourth quarter."),
    ("gross_margin", "We expect hardware gross margin to be in the range of 30% to 32% for fiscal 2027."),
]


@pytest.mark.parametrize("metric,sentence", CHANGE_AFTER_THE_AMOUNT + PART_OF_THE_METRIC)
def test_I2_an_amount_that_is_not_the_companys_level_is_never_extracted(metric, sentence):
    from marketlens.domain.guidance import extract

    items = [i for i in extract(sentence) if i.metric == metric]
    assert all(i.status != "EXTRACTED" for i in items), [(i.low, i.high, i.status, i.period_label) for i in items]


def test_I2_end_to_end_a_segment_line_before_the_total_is_not_the_revenue_guidance(tmp_path):
    """Total revenue guidance 54.0 billion ±2% against a pre-release consensus of 54.0 billion, a quarter that beat.
    1b48217 takes the data-center line (40.0 billion) as the next quarter's revenue guidance: −26% → BEAT_WEAK_GUIDE."""
    from marketlens.application.estimate_book import attach_guidance
    from marketlens.domain.earnings import EarningsReport, assess_earnings
    from marketlens.domain.estimates import EstimateObservation
    from marketlens.domain.guidance import extract

    text = ("Fourth quarter outlook. We expect data center revenue of approximately $40.0 billion. "
            "Total revenue is expected to be $54.0 billion, plus or minus 2%.")
    st = _store(tmp_path)
    st.save_guidance("ABC", "0000-26-000041", datetime(2026, 11, 5, 21, tzinfo=UTC), "https://www.sec.gov/x", extract(text))
    st.save_estimates([EstimateObservation("ABC", "finnhub", "2027Q1", "quarter", None, date(2026, 11, 1), eps=1.47, revenue=54.0e9,
                                           report_date=date(2027, 2, 5))])
    reports = [EarningsReport(date(2026, 11, 5), "Q3 FY2026", "finnhub", revenue_actual=51.0e9, revenue_consensus=50.0e9, eps_actual=1.52, eps_consensus=1.40)]
    rep = attach_guidance(reports, st.guidance("ABC", datetime(2026, 11, 6, tzinfo=UTC)), st.estimate_history("ABC", date(2026, 11, 6)), date(2026, 11, 6))
    a = assess_earnings(rep)
    assert a.guide_rev_vs_cons is None or abs(a.guide_rev_vs_cons) < 0.05, (a.guide_rev_vs_cons, a.result_quality)


# ============================================================================ I3 (P2) an empty answer for the latest session is never asked again
class _EveningGrouped:
    """Grouped daily bars that are published some time after the close: asked on the evening of that same session,
    the day comes back empty; asked later, it has rows."""
    name = "polygon"
    configured = True

    def __init__(self) -> None:
        self.asked_on: date | None = None

    def get_grouped_daily(self, d: date) -> dict:
        from marketlens.domain.market import Bar

        return {} if d == self.asked_on else {"TTT": Bar(d, 50, 51, 49, 50, 1e6)}


def test_I3_a_session_that_was_empty_right_after_its_close_is_loaded_later(tmp_path):
    """The scheduler syncs at its first after-hours tick (a few minutes after 16:00 ET). If the free provider has not
    published the session yet, the answer is empty and the sync records the day in ``grouped_empty_days`` — meant for
    days outside the history window — so no later sync asks for it again: that session stays missing for every
    stock (bars, close-based stops, outcomes, paper trading). Whether Polygon's free tier answers empty at that
    minute was not verified; the code has no way back either way."""
    from types import SimpleNamespace

    from marketlens.application.sync import MarketSync
    from marketlens.domain.enums import Exchange
    from marketlens.domain.market import Security

    class _Chain:
        def __init__(self, providers: list, listing: list | None = None) -> None:
            self.providers, self._listing = providers, listing

        def call(self, method: str, key: object, *a: object) -> SimpleNamespace:
            return SimpleNamespace(value=self._listing)

    grouped = _EveningGrouped()
    listing = [Security("TTT", "TTT", Exchange.NASDAQ, "Unknown", "Unknown", None, cik=3)]
    reg = SimpleNamespace(chain=lambda kind: {"price": _Chain([grouped]), "universe": _Chain([], listing)}.get(kind, _Chain([])))
    st = _store(tmp_path)
    sync = MarketSync(reg, st)  # type: ignore[arg-type]
    for day, evening in ((date(2026, 6, 1), datetime(2026, 6, 1, 20, 5, tzinfo=UTC)), (date(2026, 6, 2), datetime(2026, 6, 2, 20, 5, tzinfo=UTC)),
                         (date(2026, 6, 3), datetime(2026, 6, 3, 20, 5, tzinfo=UTC))):
        grouped.asked_on = day  # each evening: that session is not published yet, every earlier one is
        sync.run(evening, backfill_days=10)
    days = {b.day for b in st.bars("TTT", date(2026, 5, 20), date(2026, 6, 3))}
    assert {date(2026, 6, 1), date(2026, 6, 2)} <= days, sorted(days)
# ============================================================================ I4 (P3) a backfilled last trading day erases the delisting
def test_I4_a_backfilled_last_session_does_not_erase_a_delisting(tmp_path):
    """XYZ trades for the last time on 2026-06-02 (a cash merger closes). That evening the bar download fails, so the
    universe step sees no bar for XYZ and records the delisting on 06-02. The next sync backfills 06-02 — XYZ's last
    trade. delisted_on() counts a bar ON the delisting day as trading after it (>=), so 1b48217 answers None from
    then on: outcomes and paper positions of XYZ never close at the last price."""
    from marketlens.domain.enums import Exchange
    from marketlens.domain.market import Bar, Security
    from marketlens.infrastructure.db import repository as repo

    st = _store(tmp_path)
    sec = Security("XYZ", "XYZ", Exchange.NASDAQ, "Unknown", "Unknown", None, cik=9)
    d0, d1 = date(2026, 6, 1), date(2026, 6, 2)
    st.save_grouped(d0, {"XYZ": Bar(d0, 20, 20, 20, 20, 1e6)}, "polygon")
    st.sync_universe([sec], d0)
    st.sync_universe([], d1)  # no bar for 06-02 yet (the download failed), XYZ gone from the SEC file
    st.save_grouped(d1, {"XYZ": Bar(d1, 25, 25, 25, 25, 5e6)}, "polygon")  # backfilled at the next sync
    with st.sf() as s:
        assert repo.delisted_on(s, "XYZ", "LIVE") is not None, "a company that stopped trading is no longer recorded as delisted"


# ============================================================================ I5 (P3) a key saved to the keychain loses to an older .env line
def test_I5_a_key_saved_from_the_setup_screen_is_the_key_used_after_the_restart(tmp_path, monkeypatch):
    """Windows setup installs keyring, so the setup screen stores API keys in the OS keychain. A key that is also in
    the installation's .env (set up by hand before this screen existed, or saved while keyring was missing) keeps its
    old line there, and at startup .env is loaded into the environment, which _secret reads first. 1b48217: the screen
    says "저장했습니다", the app keeps calling the provider with the old key."""
    from marketlens import config

    store: dict[tuple[str, str], str] = {}
    fake = types.ModuleType("keyring")
    fake.set_password = lambda svc, name, v: store.__setitem__((svc, name), v)  # type: ignore[attr-defined]
    fake.get_password = lambda svc, name: store.get((svc, name))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake)
    env = tmp_path / ".env"
    env.write_text('FINNHUB_API_KEY="old-key"\n', encoding="utf-8")
    assert config.save_setup({"FINNHUB_API_KEY": "new-key"}, env) == {"FINNHUB_API_KEY": "keychain"}
    environ = {k: v for k, v in os.environ.items() if k != "FINNHUB_API_KEY"}
    environ["MARKETLENS_ENV_FILE"] = str(env)
    monkeypatch.setattr(os, "environ", environ)  # a fresh process environment for the "restart"
    config._load_dotenv()
    assert config._secret("FINNHUB_API_KEY") == "new-key"


# ============================================================================ I6 (P3) the position plan when the name is already above its cap
def test_I6_a_position_already_over_the_single_name_limit_is_not_given_a_negative_amount():
    """ADD with a 15% holding and a 10% single-name limit. 1b48217: shares 0 and the note
    "권장 금액 $-5,000이 1주 가격보다 작음" — the screen shows a negative dollar amount instead of saying the limit is reached."""
    from marketlens.domain.portfolio import position_plan

    p = position_plan("ADD", None, 100_000.0, 50.0, 45.0, current_value=15_000.0)
    assert p is None or all("$-" not in n for n in p.notes), p
