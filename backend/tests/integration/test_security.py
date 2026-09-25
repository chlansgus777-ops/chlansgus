"""Security regressions: static-file containment, local-only API protection, secret redaction, migrations."""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from marketlens.api.app import create_app, safe_static_path
from marketlens.infrastructure.logging import SecretRedactor, configure_logging
from tests.integration.test_service_api import make_service


@pytest.fixture(scope="module")
def dist(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("site")
    d = root / "dist"
    (d / "assets").mkdir(parents=True)
    (d / "index.html").write_text("<html>INDEX</html>")
    (d / "assets" / "app.js").write_text("console.log('app')")
    (root / "secret.txt").write_text("TOP-SECRET")  # sibling of dist: must never be served
    return d


TRAVERSALS = [
    "../secret.txt", "assets/../../secret.txt", "..\\secret.txt", "assets\\..\\..\\secret.txt", "/etc/passwd", "C:/Windows/win.ini",
    "C:\\Windows\\win.ini", "~/.ssh/id_rsa", "..%2fsecret.txt", "%2e%2e/secret.txt", "%252e%252e%252fsecret.txt", "assets/%2e%2e/%2e%2e/secret.txt",
    "assets/app.js\x00.png", "....//secret.txt/..", "assets/../index.html/../../secret.txt",
]


@pytest.mark.parametrize("path", TRAVERSALS)
def test_static_path_containment(dist, path):
    assert safe_static_path(dist, path) is None


def test_static_path_allows_real_files_only(dist):
    assert safe_static_path(dist, "assets/app.js") == (dist / "assets" / "app.js").resolve()
    assert safe_static_path(dist, "assets") is None  # directories are not files
    assert safe_static_path(dist, "missing.js") is None


def test_symlink_escaping_dist_is_refused(dist):
    link = dist / "assets" / "escape.txt"
    try:
        link.symlink_to(dist.parent / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform")
    assert safe_static_path(dist, "assets/escape.txt") is None


@pytest.fixture(scope="module")
def client(dist):
    svc = make_service(universe=60)
    app = create_app(svc.settings, service=svc, run_migrations=False, dist_dir=dist)
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("raw", ["/../secret.txt", "/assets/../../secret.txt", "/%2e%2e/secret.txt", "/..%2fsecret.txt", "/%252e%252e%252fsecret.txt",
                                 "/assets/%2e%2e%2f%2e%2e%2fsecret.txt", "/..%5csecret.txt", "/assets%5c..%5c..%5csecret.txt"])
def test_http_traversal_never_serves_files_outside_dist(client, raw):
    r = client.get(raw)
    assert "TOP-SECRET" not in r.text
    assert r.status_code in (200, 404) and (r.status_code == 404 or "INDEX" in r.text)


def test_spa_fallback_only_returns_index(client):
    assert client.get("/stocks/NVDA").text == "<html>INDEX</html>"
    assert client.get("/assets/app.js").text.startswith("console.log")
    assert client.get("/secret.txt").status_code == 404  # a file-like path that is not inside dist → 404, not index
    assert client.get("/api/does-not-exist").status_code == 404


def test_dns_rebinding_host_is_rejected(client):
    assert client.get("/api/system", headers={"host": "evil.example.com"}).status_code == 400
    assert client.get("/api/system", headers={"host": "127.0.0.1:8765"}).status_code == 200


def test_foreign_origin_is_rejected(client):
    assert client.get("/api/system", headers={"origin": "https://evil.example.com"}).status_code == 403
    assert client.get("/api/system", headers={"origin": "http://localhost:5173"}).status_code == 200


def test_state_changing_requests_need_the_client_header(client):
    r = client.post("/api/watchlist/NVDA")  # a cross-site form/fetch cannot add custom headers
    assert r.status_code == 403 and "x-marketlens-client" in r.json()["detail"]
    assert client.post("/api/watchlist/NVDA", headers={"X-MarketLens-Client": "ui"}).status_code == 200


def test_invalid_ticker_is_rejected(client):
    assert client.post("/api/watchlist/..%2f..%2fetc", headers={"X-MarketLens-Client": "ui"}).status_code in (404, 405, 422)  # refused, never 200
    assert client.get("/api/stocks/NV DA;DROP").status_code == 422


def test_readiness_and_liveness(client):
    assert client.get("/api/health/live").json()["live"] is True
    r = client.get("/api/health/ready")
    assert r.status_code == 200 and r.json()["ready"] is True and r.json()["mode"] == "MOCK"


def test_optional_api_token(dist, monkeypatch):
    monkeypatch.setenv("MARKETLENS_API_TOKEN", "per-launch-token-123")
    svc = make_service(universe=60)
    app = create_app(svc.settings, service=svc, run_migrations=False, dist_dir=dist)
    with TestClient(app) as c:
        assert c.get("/api/system").status_code == 401
        assert c.get("/api/system", headers={"X-MarketLens-Token": "wrong"}).status_code == 401
        assert c.get("/api/system", headers={"X-MarketLens-Token": "per-launch-token-123"}).status_code == 200
        assert c.get("/api/health/ready").status_code == 200  # the shell polls readiness before it has the UI


# ---------------------------------------------------------------- redaction


def _capture(fn) -> str:  # type: ignore[no-untyped-def]
    buf = io.StringIO()
    configure_logging("INFO", ["sk-ant-secretvalue-123456", "fh-SECRET-777777"])
    root = logging.getLogger()
    for h in root.handlers:
        if getattr(h, "_marketlens", False):
            h.stream = buf  # type: ignore[attr-defined]
    fn()
    return buf.getvalue()


def test_third_party_http_logs_are_redacted():
    out = _capture(lambda: logging.getLogger("httpx").warning("HTTP Request: GET https://finnhub.io/api/v1/quote?symbol=NVDA&token=fh-SECRET-777777 \"HTTP/1.1 200 OK\""))
    assert "fh-SECRET-777777" not in out and "REDACTED" in out


def test_nested_payloads_headers_and_exceptions_are_redacted():
    log = logging.getLogger("marketlens.test")

    def emit() -> None:
        log.info("call", extra={"fields": {"request": {"headers": {"Authorization": "Bearer abcdefghijk", "x-api-key": "sk-ant-secretvalue-123456"}, "params": ["api_key=zzz999"]}, "input_tokens": 1234}})
        try:
            raise RuntimeError("upstream failed for https://api.stlouisfed.org/fred?api_key=LEAKED123&series_id=DGS10")
        except RuntimeError:
            log.exception("boom")

    out = _capture(emit)
    for secret in ("abcdefghijk", "sk-ant-secretvalue-123456", "zzz999", "LEAKED123"):
        assert secret not in out
    first = json.loads(out.splitlines()[0])
    assert first["input_tokens"] == 1234  # counters are not secrets


def test_redactor_handles_plain_strings():
    r = SecretRedactor(["mysecretvalue"])
    assert r.redact("token=abc&key=mysecretvalue") == "token=***REDACTED***&key=***REDACTED***"


# ---------------------------------------------------------------- migrations


def test_migrations_match_the_models(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext

    from marketlens.infrastructure.db.models import Base
    from marketlens.infrastructure.db.session import make_engine, migrate

    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    migrate(url)
    eng = make_engine(url)
    with eng.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    assert diff == [], diff
