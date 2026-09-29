"""The phone on the home Wi-Fi (application/phone.py, api/app.py): off by default; let in once with the code the PC
shows; only home-network addresses; read-only; removable on the PC; the PC's settings never reachable from a phone."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from marketlens.api.app import create_app
from tests.integration.test_service_api import make_service

H = {"X-MarketLens-Client": "test"}
PC_ADDR = "http://192.168.0.10:8766"


@pytest.fixture()
def world():  # noqa: ANN201
    svc = make_service(universe=40)
    app = create_app(svc.settings, service=svc, run_migrations=False)
    with TestClient(app, headers=H) as pc:
        app.state.phone.server = None  # no real socket in these tests (test_the_listener_starts_and_stops opens one)
        phone = TestClient(app, base_url=PC_ADDR, client=("192.168.0.23", 51000))
        yield pc, phone, app


def pair(pc: TestClient, phone: TestClient) -> None:
    code = pc.post("/api/phone/code").json()["code"]
    r = phone.post("/api/phone/pair", json={"code": code, "name": "갤럭시"}, headers=H)
    assert r.status_code == 200, r.text


def test_off_by_default_and_nothing_served_to_a_phone(world):
    pc, phone, _app = world
    assert pc.get("/api/phone").json()["enabled"] is False
    assert phone.get("/api/system").status_code == 403
    assert phone.get("/api/phone/hello").status_code == 403


def test_a_phone_is_let_in_with_the_code_and_can_only_look(world):
    pc, phone, _app = world
    st = pc.put("/api/phone", json={"enabled": True}).json()
    assert st["enabled"] and st["port"] == 8766
    # not paired: the screens' data is refused, the page can ask to pair
    assert phone.get("/api/phone/hello").json() == {"phone": True, "paired": False, "enabled": True}
    r = phone.get("/api/opportunities")
    assert r.status_code == 401 and r.json()["pair"] is True
    pair(pc, phone)
    assert phone.get("/api/phone/hello").json()["paired"] is True
    assert phone.get("/api/system").status_code == 200
    assert phone.get("/api/portfolio/live").status_code == 200
    # read-only: every change is refused, whatever it is
    r = phone.put("/api/portfolio", json={"holdings": []}, headers=H)
    assert r.status_code == 403 and r.json()["read_only"] is True
    assert phone.post("/api/watchlist/NVDA", headers=H).status_code == 403
    # the PC's own settings: never from a phone, even a paired one
    assert phone.get("/api/phone").status_code == 403
    assert phone.put("/api/phone", json={"enabled": False}, headers=H).status_code == 403
    # the per-launch desktop token is not what lets a phone in, and the phone's pages are never cached
    assert phone.get("/api/system").headers["cache-control"] == "no-store"


def test_the_code_is_single_use_limited_and_expires_with_mistakes(world):
    pc, phone, _app = world
    pc.put("/api/phone", json={"enabled": True})
    code = pc.post("/api/phone/code").json()["code"]
    wrong = "000000" if code != "000000" else "111111"
    r = phone.post("/api/phone/pair", json={"code": wrong}, headers=H)
    assert r.status_code == 400 and "맞지 않습니다" in r.json()["detail"]
    assert phone.post("/api/phone/pair", json={"code": code}, headers=H).status_code == 200
    other = TestClient(phone.app, base_url=PC_ADDR, client=("192.168.0.24", 51000))
    assert other.post("/api/phone/pair", json={"code": code}, headers=H).status_code == 400  # used once already
    # five wrong codes: the code is gone and the address waits
    code = pc.post("/api/phone/code").json()["code"]
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        other.post("/api/phone/pair", json={"code": wrong}, headers=H)
    r = other.post("/api/phone/pair", json={"code": code}, headers=H)
    assert r.status_code == 400 and "1분" in r.json()["detail"]


def test_only_the_home_network_and_the_pcs_ip_address(world):
    pc, _phone, app = world
    pc.put("/api/phone", json={"enabled": True})
    outside = TestClient(app, base_url=PC_ADDR, client=("8.8.8.8", 51000))
    assert outside.get("/api/phone/hello").status_code == 403
    by_name = TestClient(app, base_url="http://evil.example.com:8766", client=("192.168.0.23", 51000))
    assert by_name.get("/api/phone/hello").status_code == 400  # a DNS name: refused (no rebinding through the phone listener)


def test_a_removed_phone_is_out_and_switching_off_closes_it(world):
    pc, phone, _app = world
    pc.put("/api/phone", json={"enabled": True})
    pair(pc, phone)
    devs = pc.get("/api/phone").json()["devices"]
    assert [d["name"] for d in devs] == ["갤럭시"] and "hash" not in devs[0]
    pc.delete(f"/api/phone/devices/{devs[0]['id']}")
    assert phone.get("/api/system").status_code == 401
    pair(pc, phone)
    pc.put("/api/phone", json={"enabled": False})
    assert phone.get("/api/system").status_code == 403


def test_the_listener_starts_and_stops(world):
    import socket
    import urllib.request

    from marketlens.api.phone_server import PhoneServer

    _pc, _phone, app = world
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    srv = PhoneServer(app, port)
    srv.start()
    try:
        assert srv.listening and srv.error is None
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health/live", timeout=5) as r:  # noqa: S310 - local test server
            assert r.status == 200
    finally:
        srv.stop()
    assert not srv.listening
    busy = socket.socket()
    busy.bind(("0.0.0.0", 0))  # noqa: S104
    busy.listen()
    try:
        taken = PhoneServer(app, busy.getsockname()[1])
        taken.start()
        assert not taken.listening and "포트" in (taken.error or "")
    finally:
        busy.close()
