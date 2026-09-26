"""Evaluation 2 item 12: no per-ticker SEC call inside a scan; bounded, audited ingestion with back-off."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from marketlens.application.data_access import FUNDAMENTALS, DataAccess
from marketlens.application.registry import ProviderRegistry
from marketlens.domain.enums import DataMode, Exchange
from marketlens.domain.fundamentals import QuarterlyFinancials
from marketlens.domain.market import Bar, Security
from marketlens.infrastructure.health import HealthRegistry
from marketlens.providers.contracts import NotSupported, ProviderUnavailable
from marketlens.providers.router import ProviderChain
from tests.acceptance_a_grade.test_research_integrity import _store

NOW = datetime(2026, 9, 25, 15, tzinfo=timezone.utc)


class FakeSEC:
    name, mode, configured = "sec-edgar", DataMode.LIVE, True

    def __init__(self, fail: dict[str, Exception] | None = None) -> None:
        self.calls: list[str] = []
        self.fail = fail or {}

    def get_quarterly(self, t: str) -> list[QuarterlyFinancials]:
        self.calls.append(t)
        if t in self.fail:
            raise self.fail[t]
        return [QuarterlyFinancials(date(2026, 6, 30), date(2026, 8, 1), "Q2", "sec-edgar", revenue=1e9)]


def _reg(sec: FakeSEC) -> ProviderRegistry:
    h = HealthRegistry()
    return ProviderRegistry(DataMode.LIVE, h, {"fundamental": ProviderChain("fundamental", [sec], DataMode.LIVE, h, sleep=lambda _s: None)})


def _da(tmp_path, sec: FakeSEC, clock: list[datetime]) -> DataAccess:
    return DataAccess(_reg(sec), {"fundamentals": timedelta(days=1)}, store=_store(tmp_path), now_fn=lambda: clock[0])


def test_store_only_read_never_calls_the_provider(tmp_path):
    sec = FakeSEC()
    da = _da(tmp_path, sec, [NOW])
    f = da.quarters("AAA", allow_fetch=False)
    assert f.value is None and "아직 수집되지 않음" in (f.error or "") and sec.calls == []
    da.store.save_quarters("AAA", sec.get_quarterly("AAA"))
    sec.calls.clear()
    with da.store.sf() as s:  # an old retrieval is still used by the store-only read (point-in-time data)
        from marketlens.infrastructure.db.models import FundamentalVintageRow

        for r in s.query(FundamentalVintageRow):
            r.retrieved_at = NOW - timedelta(days=60)
        s.commit()
    assert da.quarters("AAA", allow_fetch=False).value and sec.calls == []


def test_failure_is_recorded_and_backed_off(tmp_path):
    sec = FakeSEC({"BAD": ProviderUnavailable("http error: 503"), "IFRS": NotSupported("us-gaap 재무 없음 (IFRS/외국 발행사 가능성)")})
    clock = [NOW]
    da = _da(tmp_path, sec, clock)
    assert da.quarters("BAD").value is None
    first = len(sec.calls)  # the router's own retries of one request
    m = da.store.ingestion(FUNDAMENTALS, "BAD")
    assert m.status == "FAILED" and m.attempts == 1 and m.next_attempt_at == NOW + timedelta(hours=1)
    da.cache = type(da.cache)()
    clock[0] = NOW + timedelta(minutes=30)
    f = da.quarters("BAD")
    assert "재시도 대기" in (f.error or "") and len(sec.calls) == first  # no request inside the back-off
    clock[0] = NOW + timedelta(hours=2)
    da.quarters("BAD")
    m = da.store.ingestion(FUNDAMENTALS, "BAD")
    assert len(sec.calls) > first and m.attempts == 2 and m.next_attempt_at == clock[0] + timedelta(hours=2)  # 1h, 2h, 4h … ≤ 24h


def test_a_filer_without_quarterly_xbrl_is_retried_only_monthly(tmp_path):
    sec = FakeSEC({"IFRS": NotSupported("us-gaap 재무 없음 (IFRS/외국 발행사 가능성)")})
    da = _da(tmp_path, sec, [NOW])
    da.quarters("IFRS")
    m = da.store.ingestion(FUNDAMENTALS, "IFRS")
    assert m.status == "NOT_SUPPORTED" and m.next_attempt_at == NOW + timedelta(days=30)


def test_sync_ingests_a_bounded_batch_largest_first_and_records_everything(tmp_path):
    from marketlens.application.sync import MarketSync

    sec = FakeSEC({"C": ProviderUnavailable("http error: 500")})
    st = _store(tmp_path)
    today = date(2026, 9, 24)
    secs = [Security(t, t, Exchange.NASDAQ, "Tech", "x", None) for t in ("A", "B", "C", "D")]
    st.sync_universe(secs, today)
    caps = {"A": 5e9, "B": 50e9, "C": 20e9, "D": 2e9}
    for t in caps:
        st.save_bars(t, [Bar(today - timedelta(days=i), 100, 101, 99, 100, 1e6) for i in range(10)], "polygon")
    st.set_shares({t: (caps[t] / 100, today) for t in caps})
    st.refresh_market_caps()
    rep = type("R", (), {"errors": [], "fundamentals_ingested": 0, "fundamentals_failed": 0, "fundamentals_pending": 0})()
    MarketSync(_reg(sec), st)._ingest_fundamentals(sec, NOW, rep, 3, timedelta(days=7), 1e9, 2e7, today)
    assert sec.calls == ["B", "C", "A"]  # largest first, budget 3
    assert rep.fundamentals_ingested == 2 and rep.fundamentals_failed == 1 and rep.fundamentals_pending == 1
    assert st.ingestion_stats(FUNDAMENTALS) == {"OK": 2, "FAILED": 1}
    sec.calls.clear()
    MarketSync(_reg(sec), st)._ingest_fundamentals(sec, NOW + timedelta(hours=1, minutes=1), rep, 3, timedelta(days=7), 1e9, 2e7, today)
    assert sec.calls == ["C", "D"]  # never-ingested names, largest first (C after its 1h back-off); A/B are fresh


@pytest.mark.parametrize("err, status", [("all fundamental providers failed: [('sec-edgar', 'not supported: us-gaap 없음')]", "NOT_SUPPORTED"),
                                         ("all fundamental providers failed: [('sec-edgar', 'RateLimited: 429')]", "RATE_LIMITED"),
                                         ("all fundamental providers failed: [('sec-edgar', 'ProviderUnavailable: http error')]", "FAILED")])
def test_failure_classification(err, status):
    from marketlens.application.data_access import _failure_status

    assert _failure_status(err) == status


def test_scanner_bars_never_fall_back_to_per_ticker_calls_with_a_store(tmp_path):
    class Boom:
        def chain(self, _k):  # noqa: ANN001, ANN202
            raise AssertionError("per-ticker provider call from the scanner")

    da = DataAccess(Boom(), {}, store=_store(tmp_path))  # type: ignore[arg-type]
    assert da.bars_bulk(["X", "Y"], date(2026, 1, 1), date(2026, 9, 24)) == {}


def test_readiness_requires_fundamentals_coverage_excluding_20f_filers():
    import json

    from marketlens.application.readiness import evaluate

    from marketlens.application.registry import build_live_registry
    from marketlens.config import Settings

    reg = build_live_registry(Settings(mode=DataMode.LIVE, database_url="sqlite:///:memory:", sec_user_agent="MarketLens test test@example.com", llm_provider="none"))

    base = {"listed": 100, "large": 50, "bars_60": 100, "bars_200": 100, "bars_240": 100, "with_market_cap": 100, "large_with_sector": 50,
            "market_days": 250, "estimate_history_days": 10}
    sync = json.dumps({"status": "SYNC_COMPLETE"})
    thin = evaluate("LIVE", reg, {**base, "large_with_fundamentals": 20, "large_fund_not_supported": 0, "large_fund_failed": 7}, sync)
    assert thin.scanner_status == "SCANNER_NOT_READY" and any("분기 재무 수집 40%" in r and "수집 실패 7종목" in r for r in thin.scanner_reasons)
    # 10 of the 50 are 20-F filers without quarterly XBRL: 40/40 is complete coverage, not 80%
    full = evaluate("LIVE", reg, {**base, "large_with_fundamentals": 40, "large_fund_not_supported": 10, "large_fund_failed": 0}, sync)
    assert full.progress["fundamentals"] == 1.0 and not any("분기 재무" in r for r in full.scanner_reasons)


def test_backup_is_consistent_while_the_database_is_being_written(tmp_path):
    """Evaluation 3 R4: copying only marketlens.db (without -wal) during a sync corrupted 3 of 194 copies.
    The backup command uses SQLite's online backup API and checks the copy."""
    import sqlite3
    import threading

    from marketlens.infrastructure.db.session import backup_sqlite, checkpoint_sqlite

    st = _store(tmp_path)
    url = f"sqlite:///{(tmp_path / 's.db').as_posix()}"
    stop = threading.Event()

    def writer() -> None:
        i = 0
        while not stop.is_set():
            st.save_bars(f"T{i % 50}", [Bar(date(2026, 1, 1) + timedelta(days=i % 300), 1, 1, 1, 1, 1)], "polygon")
            i += 1

    th = threading.Thread(target=writer)
    th.start()
    try:
        outs = [backup_sqlite(url, tmp_path / f"b{k}.db") for k in range(15)]
    finally:
        stop.set()
        th.join()
    for o in outs:
        with sqlite3.connect(o) as c:
            assert c.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    with pytest.raises(FileExistsError):
        backup_sqlite(url, outs[0])  # never overwrites
    checkpoint_sqlite(url)
    assert not (tmp_path / "s.db-wal").exists() or (tmp_path / "s.db-wal").stat().st_size == 0
