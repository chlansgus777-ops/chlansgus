"""Invariant: every request that changes or stores something sits behind the CSRF defence — GETs included. Every GET
route of the API, called WITHOUT the client header (what a cross-site image, link or no-cors fetch would send), leaves
the database exactly as it was (every table's rows, every setting). State-changing methods without the header are
refused."""

from __future__ import annotations

import re

import pytest

SAMPLE = {"ticker": "NVDA", "rec_id": "1", "issue_id": "X", "scan_id": "1", "t": "NVDA", "path": "x"}


def _db_state(svc) -> dict[str, object]:  # noqa: ANN001
    from sqlalchemy import func, select

    from marketlens.infrastructure.db.models import Base

    out: dict[str, object] = {}
    with svc.sf() as s:
        for table in Base.metadata.sorted_tables:
            out[table.name] = s.scalar(select(func.count()).select_from(table))
        from marketlens.infrastructure.db.models import AppSettingRow

        out["settings"] = sorted((r.key, r.value) for r in s.scalars(select(AppSettingRow)))
    return out


@pytest.fixture(scope="module")
def app_and_svc():  # noqa: ANN201
    from marketlens.api.app import create_app
    from tests.integration.test_service_api import make_service

    svc = make_service(universe=60)
    svc.run_scan(run_committee=False)
    return create_app(svc.settings, service=svc, run_migrations=False), svc


def _api_routes(app):  # noqa: ANN001, ANN202
    from marketlens.api.routes import router

    return [(("/api" + r.path) if not r.path.startswith("/api") else r.path, r.methods) for r in router.routes] + \
        [(r.path, r.methods) for r in app.routes if getattr(r, "path", "").startswith("/api/")]


def _get_paths(app) -> list[str]:  # noqa: ANN001
    out = []
    for path, methods in _api_routes(app):
        r = type("R", (), {"path": path, "methods": methods})
        path = getattr(r, "path", "")
        if not path.startswith("/api/") or "GET" not in getattr(r, "methods", set()):
            continue
        out.append(re.sub(r"\{(\w+)(:[^}]*)?\}", lambda m: SAMPLE.get(m.group(1), "1"), path))
    return sorted(set(out))


def test_no_get_without_the_client_header_changes_the_database(app_and_svc):
    from fastapi.testclient import TestClient

    app, svc = app_and_svc
    paths = _get_paths(app)
    assert "/api/stocks/NVDA" in paths
    with TestClient(app) as c:
        for p in paths:
            for q in ("", "?refresh=true"):
                before = _db_state(svc)
                c.get(p + q)
                after = _db_state(svc)
                assert after == before, f"GET {p}{q} without the client header changed the database"


def test_a_state_changing_method_without_the_client_header_is_refused(app_and_svc):
    from fastapi.testclient import TestClient

    app, _svc = app_and_svc
    with TestClient(app) as c:
        for path, methods in _api_routes(app):
            for method in sorted(set(methods or ()) & {"POST", "PUT", "DELETE", "PATCH"}):
                if not path.startswith("/api/"):
                    continue
                url = re.sub(r"\{(\w+)(:[^}]*)?\}", lambda m: SAMPLE.get(m.group(1), "1"), path)
                assert c.request(method, url).status_code == 403, (method, url)


def test_the_app_itself_still_reads_and_analyses():
    # its own service: the module fixture's background workers are stopped when an earlier test's TestClient closes,
    # and this test must really start an analysis (AMD is no longer among the 20 stored candidates since 2026-10-07)
    from fastapi.testclient import TestClient

    app, _svc = app_and_svc.__wrapped__()
    with TestClient(app, headers={"X-MarketLens-Client": "web"}) as c:
        assert c.get("/api/stocks/AMD?refresh=true").status_code == 405
        assert c.post("/api/stocks/AMD/analysis").status_code == 202
        _svc.analyses.wait("analysis:AMD", 10)
        r = c.get("/api/stocks/AMD")
        assert r.status_code == 200 and r.json()["recommendation"]["ticker"] == "AMD"
