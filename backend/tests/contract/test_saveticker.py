"""Offline public-contract fixtures, constructed for tests; not downloaded SaveTicker responses."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Event
from types import SimpleNamespace

import httpx
import pytest

from marketlens.application.codec import decode, encode
from marketlens.application.issue_engine import build_issues, dedupe, title_similarity
from marketlens.application.refresher import Snapshot
from marketlens.application.registry import build_live_registry, build_mock_registry
from marketlens.config import Settings
from marketlens.domain.enums import DataMode
from marketlens.infrastructure.health import HealthRegistry
from marketlens.infrastructure.resilience import RetryConfig
from marketlens.providers.contracts import NewsItem
from marketlens.providers.router import ProviderChain
from marketlens.providers.saveticker import SaveTickerNewsProvider
from marketlens.providers.saveticker.parser import SaveTickerParseError, classify_title, extract_tickers, parse_date, parse_news
from tests.integration.test_service_api import make_service

NOW = datetime(2026, 9, 25, 15, tzinfo=timezone.utc)


def payload():
    return {"news_list": [{"id": "fixture-1", "title": "테스트 기업 가이던스 상향", "created_at": NOW.isoformat(),
                          "source": "테스트 출처", "tag_names": ["$NVDA", "$AMD", "정보"], "view_count": 12}]}


def test_public_news_normalization_does_not_invent_detail_or_financials():
    n = parse_news(payload(), NOW)[0]
    assert n.tickers == ("AMD", "NVDA")
    assert n.summary == n.body == n.url == ""
    assert n.metadata.provider == "saveticker"
    assert n.metadata.original_source == "테스트 출처"
    assert n.metadata.event_type == "guidance"
    assert n.metadata.bullish_bearish_hint == "bullish"
    assert n.metadata.view_count == 12
    assert decode(NewsItem, encode(n)) == n


def test_ticker_extraction_uses_only_explicit_tags():
    assert extract_tickers(["NVDA", "$nvda", "$BRK.B", "$AMD", "$AMD", "$CPI 발표", "정보"]) == ("AMD", "BRK.B", "NVDA")


def test_dates_preserve_original_offset_and_convert_to_utc():
    assert parse_date("2026-09-26T00:00:00+09:00") == NOW
    assert parse_date("2026-09-25T15:00:00Z") == NOW
    with pytest.raises(SaveTickerParseError, match="timezone"):
        parse_date("2026-09-25T15:00:00")


@pytest.mark.parametrize("body", [{}, {"news_list": None}, {"news_list": [{"title": "incomplete"}]}])
def test_changed_schema_is_failure_not_empty_news(body):
    with pytest.raises(SaveTickerParseError): parse_news(body, NOW)


def test_future_news_is_rejected():
    p = payload(); p["news_list"][0]["created_at"] = (NOW + timedelta(days=1)).isoformat()
    with pytest.raises(SaveTickerParseError, match="future"): parse_news(p, NOW)


def test_empty_public_list_is_valid():
    assert parse_news({"news_list": []}, NOW) == []


def test_earnings_headline_is_not_an_actual_vs_estimate_report():
    p = payload(); p["news_list"][0]["title"] = "테스트 기업 실적발표"
    n = parse_news(p, NOW)[0]
    assert n.metadata.event_type == "earnings"
    assert n.metadata.bullish_bearish_hint == "unknown"
    assert not hasattr(n.metadata, "eps_actual")


def test_options_headline_is_only_news_and_rumours_have_no_direction():
    assert classify_title("테스트 옵션 거래량 증가")[0] == "options_news"
    assert classify_title("(카더라) 테스트 기업 가이던스 상향")[1] == "unknown"
    assert classify_title("가이던스 상향 아니다")[1] == "unknown"
    assert classify_title("가이던스 상향 및 가이던스 하향")[1] == "unknown"


def test_korean_dedup_retains_provenance_and_does_not_collapse_different_ai_headlines():
    n = parse_news(payload(), NOW)[0]
    primary = replace(n, news_id="primary", source_type="WIRE", metadata=None, provenance=("primary",))
    rows, dropped = dedupe([n, primary])
    assert len(rows) == 1 and dropped == 1
    assert rows[0].metadata.provider == "saveticker"
    assert rows[0].provenance == ("primary", "saveticker")
    assert title_similarity("AI 반도체 대형 계약 체결", "AI 신규 규제 발표로 사업 불확실") < .85


def test_canonical_url_duplicate_keeps_supplement_provenance():
    n = replace(parse_news(payload(), NOW)[0], url="https://example.test/story?utm_source=test")
    other = replace(n, news_id="primary", title="Translated title", source_type="WIRE", metadata=None, provenance=("primary",))
    rows, count = dedupe([n, other])
    assert count == 1 and len(rows) == 1
    assert "saveticker" in rows[0].provenance


def test_similar_korean_headlines_with_a_denial_remain_separate():
    n = parse_news(payload(), NOW)[0]
    claim = replace(n, title="테스트 기업 대형 공급 계약 체결 공식 발표", url="")
    denial = replace(claim, news_id="denial", title="테스트 기업 대형 공급 계약 체결 공식 발표 부인")
    rows, count = dedupe([claim, denial])
    assert len(rows) == 2 and count == 0


def test_optional_source_reuses_http_and_does_not_send_credentials():
    requests = []
    def handler(req):
        requests.append(req)
        return httpx.Response(200, json=payload())
    p = SaveTickerNewsProvider(httpx.MockTransport(handler), now=lambda: NOW)
    try:
        assert p.get_latest_news()[0].metadata.provider == "saveticker"
        assert requests[0].url.path == "/api/news/list"
        assert requests[0].url.params["page_size"] == "20"
        assert "authorization" not in requests[0].headers and "cookie" not in requests[0].headers
    finally: p.close()


def test_denied_public_endpoint_is_not_retried_and_does_not_erase_existing_news():
    calls = []
    def handler(req):
        calls.append(req); return httpx.Response(403)
    p = SaveTickerNewsProvider(httpx.MockTransport(handler), now=lambda: NOW)
    svc = make_service(universe=40)
    svc.registry.extras["news_supplement"] = ProviderChain("news", [p], DataMode.LIVE, svc.health, sleep=lambda _:None)
    svc.data.supplemental_news = lambda: svc.supplemental_news_view().value or []
    try:
        initial = svc.data.news(NOW - timedelta(days=1), None)
        assert initial.value
        svc.refresher.wait("news-supplement", 2)
        view = svc.news_view()
        assert view["error"] and view["last_success"] is None
        assert view["rows"] == []  # primary news remains in its existing UI, not duplicated as SaveTicker news
        assert len(calls) == 1
        assert svc.data.news(NOW - timedelta(days=1), None).value == initial.value
        assert len(calls) == 1  # failure backoff
    finally: svc.stop_background()


def test_background_singleflight_does_not_block_a_screen():
    enter, release = Event(), Event()
    def handler(req):
        enter.set(); assert release.wait(2); return httpx.Response(200, json=payload())
    p = SaveTickerNewsProvider(httpx.MockTransport(handler), now=lambda: NOW)
    svc = make_service(universe=40)
    svc.registry.extras["news_supplement"] = ProviderChain("news", [p], DataMode.LIVE, svc.health,
                                                          retry_cfg=RetryConfig(attempts=1))
    try:
        assert svc.news_view()["pending"]
        assert enter.wait(1)
        before = svc.refresher.runs
        assert svc.news_view("NVDA")["pending"]
        assert svc.refresher.runs == before
        release.set(); svc.refresher.wait("news-supplement", 2)
        assert svc.news_view("NVDA")["rows"][0]["tickers"] == ["AMD", "NVDA"]
        assert not svc.news_view("TSLA")["rows"]
    finally:
        release.set(); svc.stop_background()


def test_enabled_flag_is_live_only_and_default_off():
    st = Settings(mode=DataMode.LIVE, database_url="sqlite://", sec_user_agent=None)
    assert "news_supplement" not in build_live_registry(st).extras
    assert "news_supplement" not in build_mock_registry(universe_size=10).extras


def test_market_supplement_does_not_repeat_a_headline_already_in_issues():
    supplement = replace(parse_news(payload(), NOW)[0], title="NVDA raises guidance after earnings beat")
    primary = replace(supplement, news_id="primary", source_type="WIRE", metadata=None, provenance=("primary",))
    svc = make_service(universe=40)
    provider = SaveTickerNewsProvider(httpx.MockTransport(lambda req: httpx.Response(403)))
    svc.registry.extras["news_supplement"] = ProviderChain("news", [provider], DataMode.LIVE, svc.health)
    svc.last_scan_context = SimpleNamespace(issues=build_issues([primary], NOW))
    svc.supplemental_news_view = lambda: Snapshot([supplement], NOW, 0, False)
    try:
        assert svc.last_scan_context.issues.issues  # an actual issue, not an empty placeholder
        assert svc.news_view()["rows"] == []
        stock_rows = svc.news_view("NVDA")["rows"]
        assert len(stock_rows) == 1  # stock details have no raw headline list already
        assert stock_rows[0]["provenance"] == ["primary", "saveticker"]
    finally:
        svc.last_scan_context = None
        svc.stop_background()
