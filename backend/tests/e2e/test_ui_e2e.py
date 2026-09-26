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
    page.goto(f"{server}/#/")
    page.get_by_text("오늘의 미국 주식 한눈에 보기").wait_for()
    assert "모의 데이터(MOCK)" in page.get_by_test_id("banner-mock").inner_text()
    for title in ("오늘 시장 분위기", "지금 가장 유망한 종목", "가장 조심할 위험", "다가오는 중요한 일정", "내 포트폴리오", "모의투자 성과", "추천이 바뀐 종목", "관심 종목 알림", "시스템 상태"):
        assert page.get_by_role("heading", name=title, exact=True).is_visible(), title
    assert page.get_by_role("link", name="기회 찾기").is_visible()
    assert page.errors == []  # type: ignore[attr-defined]


def test_stock_detail_puts_the_answer_on_top(page, server):
    page.goto(f"{server}/#/opportunities")
    page.get_by_role("heading", name="기회 찾기").wait_for()
    first = page.locator("table tbody tr td a").first
    ticker = first.inner_text()
    first.click()
    page.get_by_role("heading", name="매수 계획", exact=True).wait_for()
    for title in ("좋은 이유", "주의할 이유", "현재 이슈 영향", "투자 논리가 깨지는 조건", "내 포트폴리오에 넣어도 될까?", "왜 이런 판단이 나왔나요?"):
        assert page.get_by_role("heading", name=title, exact=True).is_visible(), title
    assert ticker in page.locator("h1").first.inner_text()
    assert " ET (" in page.content() and "KST)" in page.content()  # ET with KST alongside
    hero = page.locator("section.hero")
    hero_box, plan_box = hero.bounding_box(), page.get_by_role("heading", name="매수 계획", exact=True).bounding_box()
    assert hero_box and plan_box and hero_box["y"] < plan_box["y"]  # the decision comes before the details
    # beginner mode folds raw data; advanced mode opens it
    fresh = page.locator("details", has_text="데이터 종류별 신선도")
    assert fresh.get_attribute("open") is None
    page.get_by_role("button", name="자세히 보기", exact=True).click()
    assert page.locator("details", has_text="데이터 종류별 신선도").get_attribute("open") is not None
    page.get_by_role("button", name="쉽게 보기", exact=True).click()
    assert page.errors == []  # type: ignore[attr-defined]


def test_state_changing_call_from_the_ui_passes_the_csrf_guard(page, server):
    page.goto(f"{server}/#/stocks")
    page.get_by_placeholder("종목 코드 추가").fill("NVDA")
    page.get_by_role("button", name="관심종목 추가").click()
    page.get_by_test_id("watchlist").locator("table tbody tr td a", has_text="NVDA").wait_for()
    assert page.errors == []  # type: ignore[attr-defined]


def test_narrow_window_stacks_cards(page, server):
    page.set_viewport_size({"width": 760, "height": 1000})
    page.goto(f"{server}/#/")
    page.get_by_text("오늘의 미국 주식 한눈에 보기").wait_for()
    boxes = [page.get_by_role("heading", name=t).bounding_box() for t in ("오늘 시장 분위기", "지금 가장 유망한 종목")]
    assert boxes[0] and boxes[1] and boxes[1]["y"] > boxes[0]["y"]  # one column on narrow windows
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")  # no horizontal page scroll
    page.set_viewport_size({"width": 1400, "height": 1000})
