"""Follow-ups to the 8th evaluation (I1–I6) beyond the evaluator's own counterexamples: the same split basis on the
stored-recommendation paths, the forms of guidance that must keep extracting, a recent session that is not yet
published, and the .env round trip."""

from __future__ import annotations

import sys
import types
from datetime import date, datetime, timedelta, timezone

import pytest

from tests.integration.test_service_api import make_service

UTC = timezone.utc


# ---------------------------------------------------------------- I1 on the stored-recommendation paths
def test_a_stored_recommendation_is_checked_on_the_post_split_basis(monkeypatch):
    from marketlens.api.routes import _row_summary
    from marketlens.domain.corporate_actions import SplitEvent
    from marketlens.infrastructure.db import repository as repo

    svc = make_service(universe=80)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        row = next(r for r in repo.all_recommendations(s) if (r.result.get("entry") or {}).get("stop"))
    before = _row_summary(row, svc)
    svc._clock["t"] += timedelta(days=3)  # type: ignore[attr-defined]
    split = SplitEvent(row.ticker, (svc.now() - timedelta(days=1)).date(), 1, 10, "polygon")
    monkeypatch.setattr(svc.data, "splits", lambda t: [split] if t == row.ticker else [])
    lv = svc.levels_now(row)
    assert lv["split_factor"] == 10.0 and lv["stop"] == pytest.approx(row.result["entry"]["stop"] / 10)
    after = _row_summary(row, svc)
    for k in ("price", "stop", "max_buy", "target"):
        assert after[k] == pytest.approx(before[k] / 10), k
    assert after["split_factor_since"] == 10.0
    # a split the analysis already knew (its bars reflected it) changes nothing — changed with the 9th evaluation (H1):
    # the basis is the splits recorded in the analysis inputs, no longer the analysis date; a record without that
    # list (older snapshots) still uses the date rule
    old = SplitEvent(row.ticker, date(2020, 1, 2), 1, 10, "polygon")
    monkeypatch.setattr(svc.data, "splits", lambda t: [old])
    row.inputs = {**row.inputs, "splits": [{"execution_date": "2020-01-02", "split_from": 1, "split_to": 10}]}
    assert svc.levels_now(row)["split_factor"] == 1.0 and svc.levels_now(row)["stop"] == pytest.approx(row.result["entry"]["stop"])
    row.inputs = {k: v for k, v in row.inputs.items() if k != "splits"}
    assert svc.levels_now(row)["split_factor"] == 1.0


def test_the_previous_levels_are_rescaled_only_by_splits_after_that_analysis():
    from marketlens.application.pipeline import on_current_share_basis
    from marketlens.domain.corporate_actions import SplitEvent
    from marketlens.domain.what_changed import AnalysisDigest

    prev = AnalysisDigest(ticker="T", as_of=datetime(2026, 6, 1, 20, tzinfo=UTC), score=80.0, action="HOLD", price=300.0, in_buy_zone=True,
                          stop_breached=False, rr=2.0, eps_revision_30d=None, revenue_revision_30d=None, last_earnings_date=None,
                          guidance_signature=None, issue_ids=(), major_issue_ids=(), regime=None, us10y=None, thesis_invalidated=False,
                          components={}, stop=280.0, guard_stop=270.0, max_buy=305.0, atr=6.0)
    s = SplitEvent("T", date(2026, 6, 3), 1, 3, "polygon")
    now = datetime(2026, 6, 5, 20, tzinfo=UTC)
    p = on_current_share_basis(prev, [s], now)
    assert p is not None and (p.price, p.stop, p.guard_stop, p.max_buy, p.atr) == pytest.approx((100.0, 280 / 3, 90.0, 305 / 3, 2.0))
    assert p.score == prev.score and p.rr == prev.rr  # ratios are not prices
    assert on_current_share_basis(prev, [s], datetime(2026, 6, 2, 20, tzinfo=UTC)) is prev  # not executed yet
    assert on_current_share_basis(prev, [SplitEvent("T", date(2026, 5, 1), 1, 3, "polygon")], now) is prev  # already in the old levels


# ---------------------------------------------------------------- I2: the company's own level still extracts
@pytest.mark.parametrize("sentence,lo,hi", [
    ("Revenue is expected to be $54.0 billion, plus or minus 2%.", 52.92e9, 55.08e9),
    ("We expect total revenue of approximately $3.2 billion in Q4 2026.", 3.2e9, 3.2e9),
    ("We are raising our full-year revenue outlook to $12.0 billion to $12.2 billion.", 12.0e9, 12.2e9),
    ("We now expect full-year net sales of $5.1 billion.", 5.1e9, 5.1e9),
    ("The Company expects fiscal 2027 GAAP diluted EPS of $2.40 to $2.50.", 2.40, 2.50),
    ("We expect earnings of $1.10 to $1.15 per share for the fourth quarter.", 1.10, 1.15),
    ("We expect GAAP net income of $0.50 per diluted share in fiscal 2027.", 0.50, 0.50),
    ("For the fourth quarter, we expect non-GAAP gross margin of 72% to 73%.", 0.72, 0.73),
    ("We expect revenue of $2.0 billion, up 10% year over year, for the fourth quarter.", 2.0e9, 2.0e9),
])
def test_company_level_guidance_still_extracts(sentence, lo, hi):
    from marketlens.domain.guidance import extract

    (i,) = extract(sentence)
    assert i.status == "EXTRACTED" and i.low == pytest.approx(lo) and i.high == pytest.approx(hi)


# ---------------------------------------------------------------- I3: a recent empty answer is pending, an old one is final
def test_a_recent_unpublished_session_is_pending_and_an_old_empty_day_stays_recorded(tmp_path):
    from types import SimpleNamespace

    from marketlens.application.market_store import MarketStore
    from marketlens.application.sync import MarketSync
    from marketlens.domain.market import Bar
    from marketlens.infrastructure.db.models import Base
    from marketlens.infrastructure.db.session import make_engine, make_session_factory

    today, old = date(2026, 6, 3), date(2026, 5, 20)

    class G:
        name, configured = "polygon", True

        def get_grouped_daily(self, d: date) -> dict:
            return {} if d in (today, old) else {"TTT": Bar(d, 5, 5, 5, 5, 1e6)}

    class C:
        def __init__(self, p: list) -> None:
            self.providers = p

        def call(self, *_a: object) -> SimpleNamespace:
            return SimpleNamespace(value=[])

    eng = make_engine(f"sqlite:///{(tmp_path / 's.db').as_posix()}")
    Base.metadata.create_all(eng)
    st = MarketStore(make_session_factory(eng), "LIVE")
    reg = SimpleNamespace(chain=lambda k: C([G()]) if k == "price" else C([]))
    rep = MarketSync(reg, st).run(datetime(2026, 6, 3, 20, 5, tzinfo=UTC), backfill_days=20)  # type: ignore[arg-type]
    assert rep.bar_days_pending == 1 and rep.bar_days_empty == 1 and not rep.errors
    assert rep.complete  # a session not published yet does not make the whole sync partial
    recorded = st.get_setting("grouped_empty_days") or ""
    assert old.isoformat() in recorded and today.isoformat() not in recorded


# ---------------------------------------------------------------- I5: the .env round trip
def test_a_value_saved_to_env_is_read_back_exactly(tmp_path, monkeypatch):
    import os

    from marketlens import config

    monkeypatch.setitem(sys.modules, "keyring", None)
    env = tmp_path / ".env"
    raw = r'ab\tc${HOME}"q'
    config.save_setup({"FRED_API_KEY": raw}, env)
    environ = {k: v for k, v in os.environ.items() if k != "FRED_API_KEY"}
    environ["MARKETLENS_ENV_FILE"] = str(env)
    monkeypatch.setattr(os, "environ", environ)
    config._load_dotenv()
    assert os.environ["FRED_API_KEY"] == raw


def test_a_keychain_save_leaves_the_other_env_lines_alone(tmp_path, monkeypatch):
    from marketlens import config

    store: dict[tuple[str, str], str] = {}
    fake = types.ModuleType("keyring")
    fake.set_password = lambda svc, name, v: store.__setitem__((svc, name), v)  # type: ignore[attr-defined]
    fake.get_password = lambda svc, name: store.get((svc, name))  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "keyring", fake)
    env = tmp_path / ".env"
    env.write_text('LOG_LEVEL="DEBUG"\nexport POLYGON_API_KEY="old"\nMARKETLENS_MODE="LIVE"\n', encoding="utf-8")
    assert config.save_setup({"POLYGON_API_KEY": "new"}, env) == {"POLYGON_API_KEY": "keychain"}
    assert env.read_text(encoding="utf-8") == 'LOG_LEVEL="DEBUG"\nMARKETLENS_MODE="LIVE"\n'
    assert store[("marketlens", "POLYGON_API_KEY")] == "new"


# ---------------------------------------------------------------- I6
def test_a_position_at_the_limit_is_told_so():
    from marketlens.domain.portfolio import position_plan

    p = position_plan("ADD", None, 100_000.0, 50.0, 45.0, current_value=10_000.0)  # exactly at the 10% limit
    assert p is not None and p.shares == 0 and p.amount == 0 and "한도" in p.notes[0] and "$-" not in p.notes[0]
    p = position_plan("ADD", None, 100_000.0, 50.0, 45.0, current_value=15_000.0)
    assert p is not None and "현재 15%" in p.notes[0]
