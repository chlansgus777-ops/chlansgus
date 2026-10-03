"""News-only browser delegation, using isolated data. No source account/cookies are accessed."""
from datetime import timedelta
from marketlens.application.codec import encode, decode
from marketlens.providers.contracts import NewsItem

import pytest
from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from marketlens.providers.saveticker.browser import BrowserNewsProvider
from tests.contract.test_saveticker import NOW, payload
from tests.integration.test_background_sync import _live

H = {"X-MarketLens-Client": "test"}


def test_details_enrich_existing_news_without_becoming_marketlens_summary():
    clock = [NOW]
    provider = BrowserNewsProvider(lambda: clock[0])
    provider.update(payload())
    detail = {"id": "fixture-1", "title": "테스트 기업 가이던스 상향", "created_at": NOW.isoformat(),
              "content": "외부 본문", "tickers": [{"symbol": "MU", "name": "Micron"}],
              "translations": {"source_locale": "en_US", "translated": {"ko_KR": {"summary": "외부 요약"}}}}
    provider.update_supplement("details", {"payload": {"details": [detail]}})
    item = provider.get_latest_news()[0]
    assert item.summary == "" and item.body == "외부 본문"
    assert item.metadata.provider_summary == "외부 요약"
    assert item.metadata.detail_collected_at == NOW
    assert item.tickers == ("AMD", "MU", "NVDA")
    assert item.metadata.original_source == "테스트 출처"
    assert decode(NewsItem, encode(item)) == item
    # The next ordinary news poll must not lose its independently cached detail.
    clock[0] += timedelta(seconds=120)
    provider.update(payload())
    assert provider.get_latest_news()[0].metadata.provider_summary == "외부 요약"
    assert provider.get_latest_news()[0].metadata.detail_collected_at == NOW
    clock[0] = NOW + timedelta(seconds=901)
    provider.update(payload())
    assert provider.get_latest_news()[0].metadata.provider_summary is None


def test_detail_only_related_ticker_news_preserves_unknown_wire_source():
    provider = BrowserNewsProvider(lambda: NOW)
    provider.update({"news_list": []})
    provider.update_supplement("details", {"payload": {"details": [
        {"id": "earnings-older", "title": "실적 기사", "created_at": (NOW-timedelta(hours=12)).isoformat(),
         "tickers": [{"symbol": "ACN", "name": "Accenture"}], "content": "실적 원문"}
    ]}})
    row = provider.get_latest_news()[0]
    assert row.tickers == ("ACN",) and row.source == "SaveTicker"
    assert row.metadata.original_source is None
    assert row.published_at == NOW-timedelta(hours=12)


@pytest.fixture()
def connected(tmp_path, monkeypatch):
    monkeypatch.setenv("MARKETLENS_API_TOKEN", "desktop-launch-secret")
    service = _live(tmp_path, live_quotes=False)
    service._now = lambda: NOW
    app = create_app(service.settings, service=service, run_migrations=False)
    with TestClient(app, headers=H | {"X-MarketLens-Token": "desktop-launch-secret"}) as client:
        yield service, client
    service.stop_background()


def test_only_owner_can_enroll_and_status_never_returns_key(connected):
    service, client = connected
    assert client.post("/api/saveticker/browser", headers={"X-MarketLens-Token": "wrong"}).status_code == 401
    first = client.post("/api/saveticker/browser").json()
    assert len(first["key"]) == 64
    assert client.post("/api/saveticker/browser").json()["key"] == first["key"]  # duplicate/lost response recovery
    assert first["key"] not in client.get("/api/saveticker/connection").text
    assert service.saveticker_connection.status()["status"] == "WAITING_BROWSER"
    assert client.get("/api/saveticker/browser/news").status_code == 405


def test_delegated_supplement_is_bounded_and_does_not_renew_news_freshness(connected):
    service, client = connected
    key = client.post("/api/saveticker/browser").json()["key"]
    response = client.post("/api/saveticker/browser/news", headers={"X-MarketLens-News-Bridge": key}, json={"resource": "calendar", "payload": {"events": [{"id": 1, "title": "Fed 일정", "event_date": "2026-10-01", "event_date_only": True}]}})
    assert response.status_code == 200 and response.json()["items_normalized"] == 1
    assert service.saveticker_connection.status()["last_received"] is None
    assert client.get("/api/saveticker/supplement").json()["resources"]["calendar"]["rows"][0]["scheduled_at"] is None
    bad = client.post("/api/saveticker/browser/news", headers={"X-MarketLens-News-Bridge": key}, json={"resource": "account", "payload": {"cash": 1}})
    assert bad.status_code == 422


def test_delegated_key_delivers_news_but_cannot_modify_other_resources(connected):
    service, client = connected
    key = client.post("/api/saveticker/browser").json()["key"]
    # Deliberately NO desktop launch token; the delegated key applies to this ONE endpoint only.
    extension = TestClient(client.app)  # do not start/stop the same application's lifespan twice
    headers = {"X-MarketLens-News-Bridge": key, "Origin": "chrome-extension://" + "a" * 32}
    result = extension.post("/api/saveticker/browser/news", headers=headers, json={"news": payload()})
    assert result.status_code == 200 and result.json()["items_normalized"] == 1
    for path in ("/api/settings/setup", "/api/portfolio", "/api/saveticker/browser", "/api/stocks/NVDA/analysis"):
        assert extension.post(path, headers=headers | H, json={}).status_code == 403  # forbidden origin
    assert extension.post("/api/saveticker/browser/news", json={"news": payload()}, headers={"X-MarketLens-News-Bridge": "wrong"}).status_code == 401
    assert extension.post("/api/saveticker/browser/news", json={"news": payload()}, headers={"X-MarketLens-News-Bridge": key, "Origin": "https://saveticker.com"}).status_code == 403
    service.refresher.wait("news-supplement", 1)
    status = client.get("/api/saveticker/connection").json()
    assert status["status"] == "CONNECTED" and status["method"] == "browser"
    assert not status["authenticated"]  # news collection doesn't assert the user's identity
    news = client.get("/api/news?ticker=NVDA").json()
    assert news["rows"] and news["transport_mode"] == "browser"
    assert news["last_success"] == NOW.isoformat()


def test_browser_disconnect_preserves_last_data_and_real_receive_time(connected):
    service, client = connected
    key = client.post("/api/saveticker/browser").json()["key"]
    headers = {"X-MarketLens-News-Bridge": key}
    assert client.post("/api/saveticker/browser/news", headers=headers, json={"news": payload()}).status_code == 200
    service.refresher.wait("news-supplement", 1)
    # An internal cache refresh MUST NOT pretend another browser download happened.
    service._now = lambda: NOW + timedelta(seconds=121)
    service.refresher.invalidate("news-supplement")
    service.news_view(); service.refresher.wait("news-supplement", 1)
    assert service.news_view()["last_success"] == NOW.isoformat()
    assert client.delete("/api/saveticker/browser").status_code == 200
    assert client.post("/api/saveticker/browser/news", headers=headers, json={"news": payload()}).status_code == 401
    news = service.news_view()
    assert news["rows"] and news["error"] and news["last_success"] == NOW.isoformat()
    assert service.saveticker_connection.status()["status"] == "DISCONNECTED"


def test_expiry_stale_and_source_failures_keep_existing_news(connected):
    service, client = connected
    key = client.post("/api/saveticker/browser").json()["key"]
    headers = {"X-MarketLens-News-Bridge": key}
    client.post("/api/saveticker/browser/news", headers=headers, json={"news": payload()})
    service.refresher.wait("news-supplement", 1)
    for code in ("AUTH_REQUIRED", "NETWORK", "FORMAT"):
        assert client.post("/api/saveticker/browser/news", headers=headers, json={"error": code}).status_code == 200
        assert service.news_view()["rows"] and service.news_view()["error"]
    client.post("/api/saveticker/browser/news", headers=headers, json={"news": payload()})
    service.refresher.wait("news-supplement", 1)
    service._now = lambda: NOW + timedelta(seconds=301)
    assert service.saveticker_connection.status()["status"] == "STALE"
    assert service.news_view()["error"] and service.news_view()["last_success"] == NOW.isoformat()
    service.saveticker_connection._browser_deadline = 0
    assert client.post("/api/saveticker/browser/news", headers=headers, json={"news": payload()}).status_code == 401


def test_bad_schema_future_dates_and_error_objects_are_rejected_without_echo(connected):
    service, client = connected
    key = client.post("/api/saveticker/browser").json()["key"]
    headers = {"X-MarketLens-News-Bridge": key}
    bad = payload(); bad["news_list"][0]["created_at"] = (NOW + timedelta(days=1)).isoformat()
    for body in ({"news": bad}, {"error": []}, {"news": {"private": "fixture-secret"}}):
        response = client.post("/api/saveticker/browser/news", headers=headers, json=body)
        assert response.status_code == 422 and "fixture-secret" not in response.text
    assert service.saveticker_connection.status()["status"] == "WAITING_BROWSER"
    assert not service.news_view()["rows"]
    response = client.post("/api/saveticker/browser/news", headers=headers, content=b" " * (256 * 1024 + 1))
    assert response.status_code == 413


def test_old_bridge_key_is_revoked_on_new_pairing(connected):
    service, client = connected
    old = client.post("/api/saveticker/browser").json()["key"]
    client.delete("/api/saveticker/browser")
    new = client.post("/api/saveticker/browser").json()["key"]
    assert old != new
    assert not service.saveticker_connection.browser_authorized(old)
    assert service.saveticker_connection.browser_authorized(new)


def test_provider_does_not_fake_fresh_data():
    stamp = [NOW]
    provider = BrowserNewsProvider(lambda: stamp[0])
    provider.update(payload())
    stamp[0] = NOW + timedelta(seconds=301)
    with pytest.raises(Exception, match="수신이 중단"): provider.get_latest_news()
