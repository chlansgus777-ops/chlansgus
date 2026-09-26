"""Live smoke-test command, exercised on the recorded-shape fixtures (never recorded as 'live verified')."""

from __future__ import annotations

import json

from tests.integration.test_live_fixture_pipeline import live  # noqa: F401


def test_live_verify_checks_every_category_with_provenance(live):  # noqa: F811
    from marketlens.application.live_verify import verify

    svc, _sync, _scan, _seen = live
    rep = verify(svc, tickers=("NVDA", "JPM", "TSM"), record=False)  # the companies the fixtures describe
    s = rep["summary"]
    for cat in ("price", "bars", "fundamentals", "ifrs", "bank", "earnings", "news", "macro", "estimates", "guidance", "short_interest"):
        assert s[cat] == "VERIFIED", (cat, rep["categories"][cat]["note"])
    for cat, v in rep["categories"].items():
        for smp in v["samples"]:
            assert smp["provider"] and smp["timestamp"] and smp["source"], (cat, smp)
    assert svc.store.get_setting("live_verified") is None  # fixtures never mark anything as live-verified


def test_missing_key_is_blocked_by_credential_not_failed():
    from marketlens.application.live_verify import _classify
    from marketlens.providers.contracts import ProviderUnavailable

    assert _classify(ProviderUnavailable("ALPHAVANTAGE_API_KEY 미설정")) == "BLOCKED_BY_CREDENTIAL"
    assert _classify(ProviderUnavailable("http error: ConnectError")) == "BLOCKED_BY_NETWORK"
    assert json.dumps({"ok": True})


def test_estimate_snapshots_are_labelled_with_the_new_york_day_observed(live):  # noqa: F811
    """Evaluation 2 P1: a consensus observed at 11:00 New York on 09-25 was stored as a 09-24 snapshot."""
    from sqlalchemy import select

    from marketlens.domain.market_calendar import last_completed_session, to_ny
    from marketlens.infrastructure.db.models import EstimateSnapshotRow

    svc, _sync, _scan, _seen = live
    ny_day = to_ny(svc.now()).date()
    with svc.sf() as s:
        days = {r.observed_on for r in s.scalars(select(EstimateSnapshotRow))}
    assert days == {ny_day}
    if last_completed_session(svc.now()) != ny_day:
        assert last_completed_session(svc.now()) not in days
