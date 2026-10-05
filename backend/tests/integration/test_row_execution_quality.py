"""A list row says whether the call can be acted on from its core fields (owner 2026-10-05, MU: a minute-old BUY read
"매수 · 만료 · 충돌" because a side field — news, an estimate — disagreed between sources). ``execution_quality`` is the
core-field quality the screens expire a call on; ``data_quality`` stays the overall one and is still shown."""

from marketlens.api.routes import _row_summary
from marketlens.infrastructure.db import repository as repo
from tests.integration.test_service_api import make_service


def test_a_side_field_conflict_does_not_expire_the_row_but_a_stale_core_field_does():
    svc = make_service(universe=30)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        row = repo.recommendations_for_scan(s, repo.latest_scan(s).id)[0]
        fields = [list(f) for f in row.result["data_quality"]["fields"]]
        core = {"price", "price_history", "fundamentals"}
        side = next(f for f in fields if f[0] not in core)
        side[1] = "CONFLICTING"
        row.result = {**row.result, "data_quality": {**row.result["data_quality"], "fields": fields}}
        row.data_quality = "CONFLICTING"  # the overall quality follows the worst field
        out = _row_summary(row)
        assert out["data_quality"] == "CONFLICTING"
        assert out["execution_quality"] == "FRESH"
        for f in fields:
            if f[0] == "price_history":
                f[1] = "STALE"
        row.result = {**row.result, "data_quality": {**row.result["data_quality"], "fields": fields}}
        assert _row_summary(row)["execution_quality"] == "STALE"
