"""Browser end-to-end: the built Korean UI served by the real backend (MOCK service) in headless Chromium.

Skipped when Playwright/Chromium or the built frontend (frontend/dist) is not available."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"
pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(), reason="frontend not built (npm run build)")


@pytest.fixture(scope="module")
def server():
    import uvicorn

    from marketlens.api.app import create_app
    from marketlens.workers.runtime import choose_port
    from tests.integration.test_service_api import make_service

    svc = make_service(universe=80)
    svc.run_scan(run_committee=True)
    app = create_app(svc.settings, service=svc, run_migrations=False, dist_dir=DIST)
    port = choose_port("127.0.0.1", 0)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(timeout=5)


@pytest.fixture(scope="module")
def page(server):
    import os

    with pw.sync_playwright() as p:
        browser = None
        # prefer Playwright's own browser; fall back to a system/provisioned Chromium binary
        for exe in (None, os.environ.get("MARKETLENS_E2E_CHROMIUM"), "/opt/pw-browsers/chromium"):
            try:
                browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
                break
            except Exception:  # noqa: BLE001 - try the next candidate
                continue
        if browser is None:
            pytest.skip("chromium unavailable")
        pg = browser.new_page(locale="ko-KR", timezone_id="Asia/Seoul")
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors  # type: ignore[attr-defined]
        yield pg
        browser.close()


def test_dashboard_answers_the_first_questions(page, server):
    # product overhaul 2026-09-28: the home screen answers "what now" first (today hero, candidates) with the
    # account, the calendar, the watchlist and the data connection in a side rail
    page.goto(f"{server}/#/")
    page.get_by_role("heading", name="지금 검토할 후보", exact=True).wait_for()
    assert "모의 데이터(MOCK)" in page.get_by_test_id("banner-mock").inner_text()
    for title in ("오늘", "지금 검토할 후보", "다가오는 일정", "관심 종목", "내 포트폴리오", "모의투자", "데이터 연결"):
        assert page.get_by_role("heading", name=title, exact=True).is_visible(), title
    assert page.get_by_role("navigation", name="주 메뉴").get_by_role("link", name="종목", exact=True).is_visible()
    assert page.errors == []  # type: ignore[attr-defined]


def test_stock_detail_puts_the_answer_on_top(page, server):
    page.goto(f"{server}/#/opportunities")  # the old address still works (redirects to 종목 → 스캔 후보)
    page.get_by_role("heading", name="종목", exact=True).wait_for()
    first = page.locator("table tbody tr td a").first
    first.wait_for()
    ticker = first.inner_text()
    first.click()
    page.get_by_role("heading", name="가격 계획", exact=True).wait_for()
    for title in ("판단 이유", "주의할 이유", "모르는 것", "현재 이슈 영향", "판단 철회 조건", "내 포트폴리오에 넣어도 될까?"):
        assert page.get_by_role("heading", name=title, exact=True).is_visible(), title
    assert ticker in page.locator("h1").first.inner_text()
    assert " ET (" in page.content() and "KST)" in page.content()  # ET with KST alongside
    hero = page.locator("section.hero")
    hero_box, plan_box = hero.bounding_box(), page.get_by_role("heading", name="가격 계획", exact=True).bounding_box()
    assert hero_box and plan_box and hero_box["y"] < plan_box["y"]  # the decision comes before the details
    # raw data is folded by default and opens where it is (the global 쉽게/자세히 switch was removed in the overhaul)
    fresh = page.locator("details", has_text="데이터 종류별 신선도").first
    assert fresh.get_attribute("open") is None
    fresh.locator("summary").first.click()
    assert fresh.get_attribute("open") is not None
    # the latest quote and the analysis-time price are two separate, labelled prices
    assert page.get_by_test_id("tile-price").is_visible() and "분석 기준가" in page.get_by_test_id("analysis-basis").inner_text()
    assert page.errors == []  # type: ignore[attr-defined]


def test_state_changing_call_from_the_ui_passes_the_csrf_guard(page, server):
    page.goto(f"{server}/#/stocks?tab=watch")
    page.get_by_label("관심 종목 코드").fill("NVDA")
    page.get_by_role("button", name="관심 종목 추가").click()
    page.get_by_test_id("watchlist").locator("table tbody tr td a", has_text="NVDA").wait_for()
    assert page.errors == []  # type: ignore[attr-defined]


def test_narrow_window_stacks_cards(page, server):
    page.set_viewport_size({"width": 760, "height": 1000})
    page.goto(f"{server}/#/")
    page.get_by_role("heading", name="지금 검토할 후보", exact=True).wait_for()
    boxes = [page.get_by_role("heading", name=t, exact=True).bounding_box() for t in ("지금 검토할 후보", "관심 종목")]  # main column, then the rail
    assert boxes[0] and boxes[1] and boxes[1]["y"] > boxes[0]["y"]  # one column on narrow windows
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")  # no horizontal page scroll
    page.set_viewport_size({"width": 1400, "height": 1000})


# ---------------------------------------------------------------- A-grade UI scenarios


def _rows(page, server):
    return page.request.get(f"{server}/api/opportunities").json()["rows"]


def _open_stock(page, server, ticker):
    page.goto(f"{server}/#/stocks/{ticker}")
    page.get_by_role("heading", name="가격 계획", exact=True).wait_for()


def test_navigation_is_simple_and_readiness_is_visible(page, server):
    page.goto(f"{server}/#/")
    page.get_by_role("heading", name="지금 검토할 후보", exact=True).wait_for()
    nav = page.get_by_role("navigation", name="주 메뉴")
    for label in ("홈", "종목", "포트폴리오", "시장", "성과"):
        assert nav.get_by_role("link", name=label, exact=True).is_visible(), label
    assert nav.get_by_role("link", name="AI 위원회").count() == 0  # the committee lives on the stock page (AI 검토)
    assert "연습용(모의)" in page.get_by_test_id("readiness-badge").inner_text()


def test_ticker_switch_never_shows_the_previous_stock(page, server):
    rows = _rows(page, server)
    a, b = rows[0]["ticker"], rows[1]["ticker"]
    _open_stock(page, server, a)
    assert a in page.locator("h1").first.inner_text()
    page.evaluate(f"window.location.hash = '#/stocks/{b}'")
    page.get_by_role("heading", name="가격 계획", exact=True).wait_for()
    page.wait_for_function(f"document.querySelector('h1') && document.querySelector('h1').innerText.startsWith('{b}')")
    assert a not in page.locator("h1").first.inner_text()


def test_committee_run_creates_a_reviewed_version(page, server):
    target = next(r for r in _rows(page, server) if (r["rank"] or 0) > 20 and r["committee_status"] == "NOT_RUN" and r["action"] != "DATA INSUFFICIENT")
    _open_stock(page, server, target["ticker"])
    page.get_by_role("button", name="AI 검토 실행").click()
    page.get_by_text("AI 검토본 v2").wait_for(timeout=30000)
    assert page.errors == []  # type: ignore[attr-defined]


def test_late_committee_response_cannot_leak_into_another_stock(page, server):
    import time as _t

    later = [r for r in _rows(page, server) if (r["rank"] or 0) > 20 and r["committee_status"] == "NOT_RUN" and r["action"] != "DATA INSUFFICIENT"]
    c, d = later[1]["ticker"], later[2]["ticker"]

    def slow(route):  # noqa: ANN001
        _t.sleep(1.5)
        route.continue_()

    page.route("**/api/recommendations/*/committee", slow)
    try:
        _open_stock(page, server, c)
        page.get_by_role("button", name="AI 검토 실행").click()
        page.evaluate(f"window.location.hash = '#/stocks/{d}'")  # leave before the response arrives
        page.wait_for_function(f"document.querySelector('h1') && document.querySelector('h1').innerText.startsWith('{d}')")
        page.wait_for_timeout(2500)
        assert d in page.locator("h1").first.inner_text()
        assert page.get_by_text("AI 검토본").count() == 0  # D has no committee; C's result never appears here
        assert page.get_by_text("아직 실행하지 않았습니다").is_visible()
    finally:
        page.unroute("**/api/recommendations/*/committee")


def test_beginner_explanations_are_on_screen_not_only_in_tooltips(page, server):
    _open_stock(page, server, _rows(page, server)[0]["ticker"])
    note = page.get_by_test_id("confidence-note")
    assert note.is_visible() and "주가 상승 확률이 아니라" in note.inner_text()
    assert page.get_by_text("종가 기준 이탈").first.is_visible()


def _patched(page, server, ticker, mutate):
    import json as _json

    def handler(route):  # noqa: ANN001
        resp = route.fetch()
        body = resp.json()
        mutate(body)
        route.fulfill(status=200, content_type="application/json", body=_json.dumps(body))

    page.route(f"**/api/stocks/{ticker}", handler)


def test_data_insufficient_says_what_is_missing(page, server):
    t = _rows(page, server)[3]["ticker"]

    def mutate(b):  # noqa: ANN001
        b["recommendation"]["action"] = "DATA INSUFFICIENT"
        b["committee"] = None
        an = b["analysis"]
        an["decision"]["vetoes"] = ["INSUFFICIENT_MODEL_COVERAGE"]
        an["fundamental_rules"]["subscore"] = None
        an["fundamental_rules"]["critical_missing"] = [an["fundamental_rules"]["items"][0]["metric"]]
        an["fundamental_rules"]["items"][0]["value"] = None

    _patched(page, server, t, mutate)
    try:
        _open_stock(page, server, t)
        card = page.get_by_test_id("data-insufficient")
        assert "핵심 데이터가 부족해 신뢰할 만한 매수/매도 판단을 제공하지 않습니다" in card.inner_text()
        assert "(핵심)" in card.inner_text()
    finally:
        page.unroute(f"**/api/stocks/{t}")


def test_stale_intraday_recommendation_is_not_presented_as_executable(page, server):
    t = _rows(page, server)[4]["ticker"]

    def mutate(b):  # noqa: ANN001
        b["recommendation"].update(action="BUY", current_status="NEEDS_REVALIDATION", actionable_now=False,
                                   current_status_reason="장중 분석 후 380분 경과 — 가격이 바뀌었을 수 있어 현재가 확인 전에는 실행 불가")
        b["committee"] = None

    _patched(page, server, t, mutate)
    try:
        _open_stock(page, server, t)
        assert "실행하지 마세요" in page.locator("section.hero").inner_text()
        assert page.get_by_text("현재가 재확인 필요").first.is_visible()
    finally:
        page.unroute(f"**/api/stocks/{t}")


def test_portfolio_input_gives_a_plain_reading(page, server):
    t = _rows(page, server)[0]["ticker"]
    page.goto(f"{server}/#/portfolio")
    page.get_by_role("heading", name="내 포트폴리오").wait_for()
    page.get_by_placeholder("종목 코드 (예: NVDA)").fill(t)
    page.get_by_placeholder("수량").fill("10")
    page.get_by_placeholder("매입 단가(USD)").fill("100")
    page.get_by_role("button", name="저장", exact=True).click()
    page.get_by_text("현금 비중은").or_(page.get_by_text("현금이")).first.wait_for()
    bar = page.locator(".alloc[role=img]").first  # the weight bar that replaced the donut (overhaul 2026-09-28)
    assert bar.is_visible() and t in (bar.get_attribute("aria-label") or "")


def test_a_trade_record_becomes_the_holding(page, server):
    """Round 10 feature a): a BUY recorded on the screen is the holding, marked as computed from the records."""
    t = _rows(page, server)[1]["ticker"]
    page.goto(f"{server}/#/portfolio")
    page.get_by_role("heading", name="거래 기록").wait_for()
    page.get_by_label("체결일").fill("2026-09-01")
    page.get_by_label("거래 종목 코드").fill(t)
    page.get_by_label("거래 수량").fill("3")
    page.get_by_label("체결 가격").fill("50")
    page.get_by_role("button", name="기록", exact=True).click()
    page.get_by_text("보유 3주 · 평단").wait_for()
    page.get_by_label("거래 수량").fill("4")  # selling more than held is refused with the reason
    page.get_by_label("거래 종류").select_option("SELL")
    page.get_by_label("체결일").fill("2026-09-02")
    page.get_by_label("거래 종목 코드").fill(t)
    page.get_by_label("거래 수량").fill("4")
    page.get_by_label("체결 가격").fill("60")
    page.get_by_role("button", name="기록", exact=True).click()
    page.get_by_text("보유 3주보다 많이 매도할 수 없음").wait_for()


def test_provider_error_is_explained_with_a_retry(page, server):
    page.route("**/api/dashboard", lambda route: route.fulfill(status=503, content_type="application/json", body='{"detail": "upstream down"}'))
    try:
        page.goto(f"{server}/#/")
        page.get_by_role("button", name="다시 시도").wait_for(timeout=20000)
    finally:
        page.unroute("**/api/dashboard")
    page.get_by_role("button", name="다시 시도").click()
    page.get_by_role("heading", name="지금 검토할 후보", exact=True).wait_for()
