"""Synthetic normal-login contracts: no real account or browser session is used."""
import json
from threading import Event

import httpx
import pytest
from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from marketlens.config import Settings, validate_setup
from marketlens.domain.enums import DataMode
from marketlens.providers.contracts import ProviderDataError
from marketlens.providers.saveticker import SaveTickerNewsProvider
from tests.contract.test_saveticker import NOW, payload
from tests.integration.test_background_sync import _live

EMAIL, PASSWORD = "test@example.test", " exact password ! "


def test_normal_login_owns_session_and_never_exports_user_info():
    calls = []
    def handler(req):
        calls.append(req)
        if req.url.path == "/api/auth/login":
            assert json.loads(req.content) == {"email": EMAIL, "password": PASSWORD}
            return httpx.Response(200, json={"user_info": {"id": "test-private-user"}},
                                  headers={"Set-Cookie": "test_session=issued-here; Path=/; Secure; HttpOnly"})
        assert req.headers["cookie"] == "test_session=issued-here"
        return httpx.Response(200, json=payload())
    provider = SaveTickerNewsProvider(httpx.MockTransport(handler), now=lambda: NOW, email=EMAIL, password=PASSWORD, rate_per_s=1000)
    try:
        rows = provider.get_latest_news()
        provider.get_latest_news()
        assert provider.authenticated and len(rows) == 1
        assert [r.url.path for r in calls] == ["/api/auth/login", "/api/news/list", "/api/news/list"]
        assert "test-private-user" not in repr(rows) and PASSWORD not in repr(provider)
    finally: provider.close()


@pytest.mark.parametrize("code", [400, 401, 403, 429, 500])
def test_failed_login_is_not_repeated_and_echoed_password_is_hidden(code):
    calls = []
    def handler(req):
        calls.append(req); return httpx.Response(code, text=f"credentials {EMAIL} {PASSWORD}")
    provider = SaveTickerNewsProvider(httpx.MockTransport(handler), email=EMAIL, password=PASSWORD, rate_per_s=1000)
    try:
        for _ in range(2):
            with pytest.raises(ProviderDataError) as err: provider.get_latest_news()
            assert EMAIL not in str(err.value) and PASSWORD not in str(err.value)
        assert len(calls) == 1 and not provider.authenticated
    finally: provider.close()


def test_expired_own_session_gets_one_normal_relogin():
    logins = gets = 0
    def handler(req):
        nonlocal logins, gets
        if req.url.path == "/api/auth/login":
            logins += 1; return httpx.Response(200, json={"user_info": {"id": "fixture"}})
        gets += 1
        return httpx.Response(401) if gets == 2 else httpx.Response(200, json=payload())
    provider = SaveTickerNewsProvider(httpx.MockTransport(handler), now=lambda: NOW, email=EMAIL, password=PASSWORD, rate_per_s=1000)
    try:
        provider.get_latest_news(); assert provider.get_latest_news()
        assert (logins, gets) == (2, 3)
    finally: provider.close()


def test_browser_security_challenge_is_not_labelled_as_wrong_password():
    provider = SaveTickerNewsProvider(httpx.MockTransport(lambda req: httpx.Response(403, text='<html><title>Just a moment...</title></html>')),
                                     email=EMAIL, password=PASSWORD)
    try:
        with pytest.raises(ProviderDataError, match="브라우저 보안 검증") as error:
            provider.get_latest_news()
        assert "계정 정보 오류는 확인되지" in str(error.value)
    finally: provider.close()


def test_server_time_policy_is_source_specific_and_preserves_raw_timestamp():
    body = payload(); body["news_list"][0]["created_at"] = "2026-09-26T00:00:00"
    provider = SaveTickerNewsProvider(httpx.MockTransport(lambda req: httpx.Response(200, json=body)), now=lambda: NOW)
    try:
        row = provider.get_latest_news()[0]
        assert row.published_at == NOW
        assert row.metadata.original_published_at == "2026-09-26T00:00:00"
        body["news_list"][0]["created_at"] = "2026-09-26T00:01:00"
        with pytest.raises(ProviderDataError, match="future"): provider.get_latest_news()
    finally: provider.close()


def test_password_whitespace_is_preserved_and_settings_repr_hides_account():
    assert validate_setup({"SAVETICKER_PASSWORD": PASSWORD})["SAVETICKER_PASSWORD"] == PASSWORD
    st = Settings(mode=DataMode.LIVE, database_url="sqlite://", sec_user_agent=None, saveticker_email=EMAIL, saveticker_password=PASSWORD)
    assert EMAIL not in repr(st) and PASSWORD not in repr(st)
    assert EMAIL in st.secrets() and PASSWORD in st.secrets()


def test_connect_returns_immediately_singleflights_and_recovers_via_get(tmp_path, monkeypatch):
    enter, release = Event(), Event()
    def handler(req):
        if req.url.path == "/api/auth/login":
            enter.set(); assert release.wait(3)
            return httpx.Response(200, json={"user_info": {"id": "fixture"}})
        return httpx.Response(200, json=payload())
    monkeypatch.setattr("marketlens.application.saveticker_connection.SaveTickerNewsProvider",
                        lambda **kw: SaveTickerNewsProvider(httpx.MockTransport(handler), now=lambda: NOW, rate_per_s=1000, **kw))
    service = _live(tmp_path, live_quotes=False)
    app = create_app(service.settings, service=service, run_migrations=False)
    try:
        with TestClient(app, headers={"X-MarketLens-Client": "test"}) as client:
            result = client.post("/api/saveticker/connection", json={"email": EMAIL, "password": PASSWORD}).json()
            assert result["started"] and result["status"] == "CONNECTING"
            assert enter.wait(1)
            assert not client.post("/api/saveticker/connection", json={"email": EMAIL, "password": PASSWORD}).json()["started"]
            assert PASSWORD not in json.dumps(result) and EMAIL not in json.dumps(result)
            release.set(); service.refresher.wait("saveticker-connect", 3); service.refresher.wait("news-supplement", 1)
            result = client.get("/api/saveticker/connection").json()
            assert result["status"] == "CONNECTED" and result["items_fetched"] == 1 and not result["saved"]
            assert service.news_view("NVDA")["rows"]
            assert client.get("/api/saveticker/connection?email=ignored&password=ignored").json() == result
    finally: release.set(); service.stop_background()


def test_invalid_sensitive_input_is_not_echoed(tmp_path):
    service = _live(tmp_path, live_quotes=False)
    app = create_app(service.settings, service=service, run_migrations=False)
    try:
        with TestClient(app, headers={"X-MarketLens-Client": "test"}) as client:
            for body in [{"email": EMAIL, "password": {"secret": PASSWORD}}, {"email": EMAIL, "password": PASSWORD, "remember": "true"}]:
                response = client.post("/api/saveticker/connection", json=body)
                assert response.status_code == 400 and PASSWORD not in response.text and EMAIL not in response.text
    finally: service.stop_background()
