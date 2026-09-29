"""/api/phone — the PC turns the phone connection on or off, shows the pairing code and removes devices; a phone
asks whether it is paired and pairs with the code (application/phone.py). Only ``PUBLIC`` is reachable from a phone
that is not paired yet; the rest is for the PC's own screens."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from marketlens.application.phone import COOKIE, PairError

router = APIRouter()
PUBLIC = {("GET", "/api/phone/hello"), ("POST", "/api/phone/pair"), ("GET", "/api/health/live")}
COOKIE_MAX_AGE = 400 * 24 * 3600  # a year and a bit (browsers cap at 400 days); removed on the PC at any time


def _link(req: Request) -> Any:
    link = getattr(req.app.state, "phone", None)
    if link is None:
        raise HTTPException(503, "폰 연결을 아직 쓸 수 없습니다(앱 시작 중)")
    return link


def _pc_only(req: Request) -> None:
    if getattr(req.state, "remote", False):
        raise HTTPException(403, "이 설정은 PC 화면에서만 바꿀 수 있습니다.")


@router.get("/phone/hello")
def hello(req: Request) -> dict[str, Any]:
    """What the phone screen needs first: is this a phone, and is it paired?"""
    link = getattr(req.app.state, "phone", None)
    remote = bool(getattr(req.state, "remote", False))
    paired = bool(remote and link is not None and link.check(req.cookies.get(COOKIE)))
    return {"phone": remote, "paired": paired, "enabled": bool(link and link.enabled)}


class PairIn(BaseModel):
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    name: str = Field(default="휴대폰", max_length=40)


@router.post("/phone/pair")
def pair(body: PairIn, req: Request) -> JSONResponse:
    link = _link(req)
    try:
        token = link.pair(body.code, body.name, req.client.host if req.client else "")
    except PairError as e:
        raise HTTPException(400, str(e)) from None
    resp = JSONResponse({"paired": True})
    # HttpOnly: the page's scripts never see it; SameSite=Strict: no other site can use it
    resp.set_cookie(COOKIE, token, max_age=COOKIE_MAX_AGE, httponly=True, samesite="strict", path="/")
    return resp


@router.get("/phone")
def status(req: Request) -> dict[str, Any]:
    _pc_only(req)
    return _link(req).status(with_code=True)


class EnableIn(BaseModel):
    enabled: bool


@router.put("/phone")
def enable(body: EnableIn, req: Request) -> dict[str, Any]:
    _pc_only(req)
    return _link(req).set_enabled(body.enabled)


@router.post("/phone/code")
def new_code(req: Request) -> dict[str, Any]:
    _pc_only(req)
    link = _link(req)
    try:
        link.new_code()
    except PairError as e:
        raise HTTPException(400, str(e)) from None
    return link.status(with_code=True)


@router.delete("/phone/devices/{dev_id}")
def revoke(dev_id: str, req: Request) -> dict[str, Any]:
    _pc_only(req)
    link = _link(req)
    if not link.revoke(dev_id):
        raise HTTPException(404, "그런 기기가 없습니다")
    return link.status(with_code=True)
