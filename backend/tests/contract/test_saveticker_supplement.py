from datetime import datetime, timezone

import pytest

from marketlens.providers.saveticker.browser import BrowserNewsProvider
from marketlens.providers.saveticker.parser import SaveTickerParseError
from marketlens.providers.saveticker.supplement import earnings, parse_calendar, parse_details, parse_reports, parse_options

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)


def test_observed_earnings_units_results_and_guidance_are_separate():
    body = "1분기 전망\n- 조정 EPS: 37.15~39.15달러 (예상: 36.02달러)\n4분기 실적\n- 조정 매출: 542억 3,000만 달러 (전년 동기: 113억 2,000만 달러, 예상: 514억 9,000만 달러)\n- 조정 EPS: 33.42달러 (전년 동기: 3.03달러, 예상: 31.83달러)\n사업 부문별 실적\n- 조정 EPS: 999달러 (예상: 1달러)"
    result = earnings(body)
    assert result["eps"]["actual"] == 33.42
    assert result["eps"]["estimate"] == 31.83
    assert result["eps"]["surprise_fraction"] == pytest.approx((33.42 - 31.83) / 31.83)
    assert result["revenue"]["actual"] == 54_230_000_000
    assert result["revenue"]["estimate"] == 51_490_000_000
    assert result["fiscal_year"] is None
    assert earnings("1분기 전망\n- 조정 EPS: 37.15달러 (예상: 36.02달러)") is None
    assert earnings("4분기 실적\n- 조정 EPS: 37~39달러 (예상: 36달러)") is None
    partial = earnings("4분기 실적\n- 조정 EPS: 37달러")["eps"]
    assert partial["actual"] == 37 and partial["estimate"] is None and partial["surprise_fraction"] is None
    assert earnings("4분기 실적\n- 조정 EPS: -1달러 (예상: 0달러)")["eps"]["surprise_fraction"] is None


def test_calendar_kst_and_date_precision_and_explicit_fed_fields():
    rows = parse_calendar({"events": [{"id": 1, "title": "월러 이사 (중립/투표권 O)", "event_date": "2026-10-01T23:00:00", "event_date_only": False,
                                      "content": [{"type": "text", "content": "<p>발언 일정</p>"}]}]}, NOW)
    assert rows[0]["scheduled_at"] == "2026-10-01T14:00:00+00:00"
    assert rows[0]["stance"] == "neutral" and rows[0]["voting_member"] is True
    row = {"id": 2, "title": "일정", "event_date": "2026-10-02", "event_date_only": True}
    other = parse_calendar({"events": [row]}, NOW)[0]
    assert other["scheduled_at"] is None and other["event_date"] == "2026-10-02"
    assert other["stance"] is None and other["voting_member"] is None
    del row["event_date_only"]
    with pytest.raises(SaveTickerParseError): parse_calendar({"events": [row]}, NOW)


def test_detail_source_summary_and_community_are_not_marketlens_analysis():
    rows = parse_details({"details": [{"id": "news_1", "title": "종목 기사", "created_at": "2026-10-01T05:00:00",
                                      "content": [{"type": "text", "content": "<p>공급자 본문</p><script>malicious()</script>"}],
                                      "translations": {"source_locale": "en", "translated": {"ko": {"summary": [{"type": "text", "content": "공급자 요약"}]}}},
                                      "tickers": [{"symbol": "MU", "name": "Micron"}], "vote_stats": {"vote_counts": {"positive": 9, "negative": 1}, "user_vote": "private"},
                                      "extra": {"source_url": "javascript:alert(1)"}, "author_id": "private"}]}, NOW)
    row = rows[0]
    assert row["provider_summary"] == "공급자 요약"
    assert row["source_text"] == "공급자 본문"
    assert row["related_companies"] == [{"ticker": "MU", "name": "Micron"}]
    assert row["community_sentiment"]["positive_percent"] == 90
    assert row["source_url"] is None
    assert "private" not in str(row)
    assert "marketlens_analysis" not in row and row["earnings"] is None


def test_resource_failure_keeps_original_timestamp_and_news_independent():
    clock = [NOW]
    browser = BrowserNewsProvider(lambda: clock[0])
    body = {"payload": {"events": [{"id": 1, "title": "일정", "event_date": "2026-10-02", "event_date_only": True}]}}
    browser.update_supplement("calendar", body)
    before = browser.supplement_view()["resources"]["calendar"]
    browser.update_supplement("calendar", {"error": "NETWORK"})
    after = browser.supplement_view()["resources"]["calendar"]
    assert after["rows"] == before["rows"] and after["last_success"] == before["last_success"]
    assert browser.received_at is None
    with pytest.raises(ValueError): browser.update_supplement({}, body)
    with pytest.raises(ValueError): browser.update_supplement("calendar", {"error": {}})
    with pytest.raises(SaveTickerParseError): browser.update_supplement("calendar", {"payload": {"events": None}})
    assert browser.supplement_view()["resources"]["calendar"]["error"] == "FORMAT"
    browser.close()
    assert browser.supplement_view()["resources"]["calendar"]["stale"]


def test_reports_do_not_invent_a_close_or_weekly_report():
    rows = parse_reports({"reports": [{"id": 1, "title": "SAVE 리포트", "created_at": "2026-10-01T05:00:00", "content": "공급자 내용"}]}, NOW)
    assert rows[0]["report_type"] == "unknown"
    with pytest.raises(SaveTickerParseError): parse_reports({"reports": [{"id": 1, "title": "리포트", "created_at": "2027-01-01", "content": ""}]}, NOW)


def test_actual_acn_unadjusted_basis_and_missing_eps_estimate():
    result = earnings("2027회계연도 전망\n- EPS: 10달러 (예상: 8달러)\n4분기 실적\n- 매출: 187억달러 (전년 대비 +6.3%, 예상: 180억4,000만달러)\n- EPS: 3.29달러 (전년 동기: 2.25달러)\n사업 부문별 실적")
    assert result["basis"] == "unspecified"
    assert result["revenue"]["basis"] == "unspecified"
    assert result["revenue"]["actual"] == 18_700_000_000
    assert result["revenue"]["estimate"] == 18_040_000_000
    assert result["revenue"]["surprise_fraction"] == pytest.approx(660_000_000 / 18_040_000_000)
    assert result["eps"]["actual"] == 3.29 and result["eps"]["estimate"] is None
    assert result["eps"]["surprise_fraction"] is None and result["fiscal_year"] is None


def test_observed_korean_locale_and_metadata_only_report():
    row = parse_details({"details": [{"id": 1, "title": "기사", "created_at": "2026-10-01T01:00:00Z",
        "translations": {"source_locale": "en_US", "translated": {"ko_KR": {"summary": "한국어 요약"}, "en_US": {"summary": "English"}}}}]}, NOW)[0]
    assert row["provider_summary"] == "한국어 요약"
    report = parse_reports({"reports": [{"id": 661, "title": "리포트", "created_at": "2026-10-01T01:00:00Z", "content": []}]}, NOW)[0]
    assert not report["has_readable_text"] and report["source_text"] == ""
    assert report["provider_url"] == "https://saveticker.com/report/661"
    assert parse_calendar({"events": [{"id": 1, "title": "쿡 이사(매파/투표권O)", "event_date": "2026-10-01", "event_date_only": True}]}, NOW)[0]["voting_member"] is True


def test_option_aggregates_preserve_missing_metrics_and_prior_day_dates():
    body = {"symbol": "MU", "optionable": True, "snapshotIsPriorDay": True, "batchIsPriorDay": True,
            "snapshotDate": "2026-09-30", "batchDate": "2026-09-30", "nearestExpiry": "2026-10-02",
            "referencePrice": 1065.11, "maxPain": 1015, "putCallRatioVolume": 0.68}
    row = parse_options({"options": [body]}, NOW)[0]
    assert row["snapshot_prior_day"] and row["batch_prior_day"]
    assert row["metrics"]["volume"] is None and row["metrics"]["referencePrice"] == 1065.11
    assert row["data_kind"] == "aggregate_only" and "live" not in row and "iv" not in row
    for bad in (float("nan"), float("inf"), True, "1"):
        with pytest.raises(SaveTickerParseError): parse_options({"options": [{**body, "volume": bad}]}, NOW)
    with pytest.raises(SaveTickerParseError): parse_options({"options": [{**body, "batchDate": "invalid"}]}, NOW)
