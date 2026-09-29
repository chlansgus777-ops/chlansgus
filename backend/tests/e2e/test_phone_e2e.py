"""The phone flow in a real browser (owner 2026-09-30: the phone showed "백엔드가 시작되지 않았습니다" — the desktop build
asked 127.0.0.1, i.e. the phone itself). The PC's phone listener serves the built app on this machine's network
address; Chromium opens it as the phone does: pairing screen, the code, then the app, read-only.
Skipped without Playwright/Chromium, the built frontend, or a private network address."""

from __future__ import annotations

import os
import socket
import time
from pathlib import Path

import pytest

pw = pytest.importorskip("playwright.sync_api")

DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def _lan_ip() -> str | None:
    from marketlens.application.phone import is_private, lan_addresses

    ips = lan_addresses()
    if ips:
        return ips[0]
    try:
        ip = socket.gethostbyname(socket.gethostname())
    except OSError:
        return None
    return ip if is_private(ip) else None


@pytest.fixture(scope="module")
def phone_site():  # noqa: ANN201
    if not (DIST / "index.html").exists():
        pytest.skip("frontend/dist not built")
    ip = _lan_ip()
    if ip is None:
        pytest.skip("no private network address on this machine")
    from marketlens.api.app import create_app
    from marketlens.api.phone_server import PhoneServer
    from tests.integration.test_service_api import make_service

    svc = make_service(universe=40)
    app = create_app(svc.settings, service=svc, run_migrations=False, dist_dir=DIST)
    from fastapi.testclient import TestClient

    with TestClient(app, headers={"X-MarketLens-Client": "test"}) as pc:  # runs the app's start-up (the phone link)
        with socket.socket() as s:
            s.bind(("0.0.0.0", 0))  # noqa: S104
            port = s.getsockname()[1]
        link = app.state.phone
        link.server = PhoneServer(app, port)
        pc.put("/api/phone", json={"enabled": True})
        assert link.server.listening, link.server.error
        yield f"http://{ip}:{port}", link
        link.server.stop()


def test_the_phone_pairs_and_sees_the_app(phone_site):
    url, link = phone_site
    with pw.sync_playwright() as p:
        browser = None
        for exe in (None, os.environ.get("MARKETLENS_E2E_CHROMIUM"), "/opt/pw-browsers/chromium"):
            try:
                browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
                break
            except Exception:  # noqa: BLE001 - try the next candidate
                continue
        if browser is None:
            pytest.skip("chromium unavailable")
        page = browser.new_page(locale="ko-KR", timezone_id="Asia/Seoul", viewport={"width": 412, "height": 915})
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(url + "/")
        # the start-up check passes and the pairing screen shows (not "백엔드가 시작되지 않았습니다")
        page.get_by_test_id("pair-screen").wait_for(timeout=20_000)
        assert "백엔드가 시작되지" not in page.content()
        page.get_by_label("연결 코드").fill(link.new_code())
        page.get_by_role("button", name="연결").click()
        page.get_by_test_id("phone-ribbon").wait_for(timeout=20_000)
        page.get_by_role("link", name="홈", exact=True).first.wait_for(timeout=20_000)
        # after a reload it stays paired (the device cookie)
        page.reload()
        page.get_by_test_id("phone-ribbon").wait_for(timeout=20_000)
        assert page.get_by_test_id("pair-screen").count() == 0
        time.sleep(1.0)
        assert not errors, errors
        browser.close()
