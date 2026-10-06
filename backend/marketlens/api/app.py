"""FastAPI application. Thin HTTP layer: no business logic lives here.

Local-only API protection (the API has no user accounts; it must only serve the local desktop UI):
- Host header allow-list (blocks DNS-rebinding attacks from web pages).
- Origin allow-list for browser requests.
- State-changing requests (POST/PUT/PATCH/DELETE) must carry the ``X-MarketLens-Client`` header. A web page
  on another origin cannot add custom headers without a CORS preflight, which is refused → no CSRF.
- Optional per-launch token (``MARKETLENS_API_TOKEN``): when set, every /api request needs it.
- Static files: the requested path is resolved and must stay inside ``frontend/dist``; everything else is
  refused. The SPA fallback only ever returns ``index.html``.
- The phone (application/phone.py, only while switched on): a client that is not this PC must come from a
  private home-network address, name the PC by its IP address, carry a paired device's cookie for the API, and can
  only read (GET) — the per-launch token and every change stay with the PC's own screens.
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.responses import PlainTextResponse

from marketlens import __version__
from marketlens.api.phone_routes import PUBLIC as PHONE_PUBLIC
from marketlens.api.phone_routes import router as phone_router
from marketlens.api.habits_routes import router as habits_router
from marketlens.api.habits_routes import wire_rule_watch
from marketlens.api.quotes import router as quotes_router
from marketlens.api.routes import router
from marketlens.application.phone import COOKIE as PHONE_COOKIE
from marketlens.application.phone import is_private
from marketlens.application.services import MarketLensService
from marketlens.config import REPO_ROOT, Settings, load_settings
from marketlens.infrastructure.db.session import make_engine, make_session_factory, migrate
from marketlens.infrastructure.logging import configure_logging

log = logging.getLogger("marketlens.api")

ALLOWED_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8765", "http://127.0.0.1:8765", "tauri://localhost", "http://tauri.localhost", "https://tauri.localhost"]
ALLOWED_HOSTS = ["localhost", "127.0.0.1", "tauri.localhost", "testserver"]
CLIENT_HEADER = "x-marketlens-client"
TOKEN_HEADER = "x-marketlens-token"
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LOCAL_CLIENTS = {"127.0.0.1", "::1", "testclient"}  # this PC (and the test client)
_BAD_PATH = re.compile(r"(^|[\\/])\.\.([\\/]|$)|\\|\x00|^[A-Za-z]:|^/|^~")


def safe_static_path(dist: Path, requested: str) -> Path | None:
    """Return the file for ``requested`` if it is a real file strictly inside ``dist``; else None.

    Rejects traversal in every spelling that reaches us after URL decoding: ``..`` segments with either
    separator, backslashes (Windows), NUL bytes, drive letters, absolute and home-relative paths — and
    finally verifies containment on the fully resolved path (symlinks included)."""
    if not requested or _BAD_PATH.search(requested) or "%" in requested:
        return None
    root = dist.resolve()
    try:
        target = (root / requested).resolve()
    except (OSError, ValueError):
        return None
    if target == root or not target.is_relative_to(root) or not target.is_file():
        return None
    return target


def create_app(settings: Settings | None = None, service: MarketLensService | None = None, run_migrations: bool = True, dist_dir: Path | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level, settings.secrets(), settings.data_dir / "logs" if run_migrations else None)
    api_token = os.environ.get("MARKETLENS_API_TOKEN") or None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.service is None:
            if run_migrations:
                migrate(settings.database_url)
            sf = make_session_factory(make_engine(settings.database_url))
            app.state.service = MarketLensService(settings, sf)
        wire_rule_watch(app.state.service)
        _start_phone(app)
        sched = None
        if getattr(settings, "scheduler", False):
            from marketlens.workers.scheduler import BackgroundScheduler

            sched = BackgroundScheduler(app.state.service)
            sched.start()
        app.state.ready = True
        diag = getattr(app.state.service, "diagnostics", None)
        if diag is not None:
            diag.start_sampler()  # the CPU every 30 s for the 진단 screen (one cheap call)
        start_quotes = getattr(app.state.service, "start_quotes", None)
        if start_quotes is not None:
            start_quotes()  # the app-wide quote stream: one connection, independent of analysis
        if getattr(app.state.service, "store", None) is not None:  # LIVE: compute the readiness counts once in the background
            import threading  # so the first dashboard / candidates screen does not wait for them

            threading.Thread(target=_warm, args=(app.state.service,), daemon=True, name="readiness-warmup").start()
        yield
        app.state.ready = False
        if diag is not None:
            diag.stop()
        if getattr(app.state, "phone", None) is not None and app.state.phone.server is not None:
            app.state.phone.server.stop()
        stop_quotes = getattr(app.state.service, "stop_quotes", None)
        if stop_quotes is not None:
            stop_quotes()
        stop_background = getattr(app.state.service, "stop_background", None)
        if stop_background is not None:
            stop_background()
        if sched:
            sched.stop()
        try:  # leave a complete .db file behind (no pending -wal content)
            from marketlens.infrastructure.db.session import checkpoint_sqlite

            checkpoint_sqlite(settings.database_url)
        except Exception as e:  # noqa: BLE001 - shutdown must not fail on a busy database
            log.warning("shutdown wal checkpoint skipped: %s", type(e).__name__)

    app = FastAPI(title="MarketLens", version=__version__, lifespan=lifespan)
    app.state.service = service
    app.state.ready = service is not None
    if service is not None:
        wire_rule_watch(service)

    app.state.phone = None

    @app.middleware("http")
    async def local_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        origin = request.headers.get("origin")
        host = request.headers.get("host", "")
        ip = request.client.host if request.client else ""
        remote = ip not in LOCAL_CLIENTS  # the phone listener: anything that is not this PC
        request.state.remote = remote
        hostname = host.rsplit(":", 1)[0] if host.count(":") <= 1 else host
        # A delegated key can ONLY deliver news on this one endpoint. It is never a desktop/phone credential.
        news_bridge = request.method == "POST" and request.url.path == "/api/saveticker/browser/news" and not remote
        if news_bridge:
            if hostname not in ALLOWED_HOSTS:
                return PlainTextResponse("Invalid host header", status_code=400)
            source = request.headers.get("origin")
            if source and not re.fullmatch(r"chrome-extension://[a-p]{32}", source):
                return JSONResponse({"detail": "허용되지 않은 브라우저 확장 출처입니다."}, status_code=403)
            service = request.app.state.service
            bridge_key = request.headers.get("X-MarketLens-News-Bridge")
            if not bridge_key:
                return JSONResponse({"detail": "뉴스 전용 연결 키가 필요합니다."}, status_code=403)
            if service is None or not service.saveticker_connection.browser_authorized(bridge_key):
                return JSONResponse({"detail": "뉴스 전용 연결 키가 없거나 만료됐습니다."}, status_code=401)
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            return response
        if remote:
            phone = app.state.phone
            if phone is None or not phone.enabled:
                return JSONResponse({"detail": "PC에서 폰 연결이 꺼져 있습니다."}, status_code=403)
            if not is_private(ip):
                return JSONResponse({"detail": "집 네트워크(같은 와이파이)에서만 연결할 수 있습니다."}, status_code=403)
            if not is_private(hostname):  # the phone names the PC by its home-network IP (never a DNS name: no rebinding)
                return PlainTextResponse("Invalid host header", status_code=400)
        elif hostname not in ALLOWED_HOSTS:
            return PlainTextResponse("Invalid host header", status_code=400)
        # same-origin requests (e.g. the UI served by this backend on any port — Vite module scripts are
        # loaded with ``crossorigin`` and carry an Origin header) are allowed; the Host header itself is
        # already restricted to localhost by TrustedHostMiddleware, so this cannot be abused via DNS rebinding
        same_origin = origin in (f"http://{host}", f"https://{host}")
        if origin is not None and origin not in ALLOWED_ORIGINS and not same_origin:
            return JSONResponse({"detail": "허용되지 않은 출처(Origin)의 요청입니다."}, status_code=403)
        path = request.url.path
        # a browser marks requests another site started (an <img>, a link, a no-cors fetch — no Origin header):
        # those never reach the API, not even a GET, since some GETs analyse and store (independent review F14)
        site = request.headers.get("sec-fetch-site")
        if path.startswith("/api/") and site in ("cross-site", "same-site") and not same_origin and origin not in ALLOWED_ORIGINS:
            return JSONResponse({"detail": "다른 사이트에서 시작된 요청은 허용되지 않습니다."}, status_code=403)
        if remote and path.startswith("/api/"):
            public = (request.method, path) in PHONE_PUBLIC
            if not public:
                dev = app.state.phone.check(request.cookies.get(PHONE_COOKIE))
                if dev is None:
                    return JSONResponse({"detail": "PC에 표시된 연결 코드로 이 폰을 먼저 연결하세요.", "pair": True}, status_code=401)
                if request.method not in ("GET", "HEAD"):
                    return JSONResponse({"detail": "폰에서는 보기만 할 수 있습니다. 바꾸는 것은 PC에서 하세요.", "read_only": True}, status_code=403)
            elif request.method in UNSAFE_METHODS and request.headers.get(CLIENT_HEADER) is None:
                return JSONResponse({"detail": f"상태 변경 요청에는 {CLIENT_HEADER} 헤더가 필요합니다."}, status_code=403)
        elif path.startswith("/api/"):
            if request.method in UNSAFE_METHODS and request.headers.get(CLIENT_HEADER) is None:
                return JSONResponse({"detail": f"상태 변경 요청에는 {CLIENT_HEADER} 헤더가 필요합니다."}, status_code=403)
            if api_token and path not in ("/api/health/live", "/api/health/ready"):
                if not hmac.compare_digest(request.headers.get(TOKEN_HEADER, ""), api_token):
                    return JSONResponse({"detail": "API 토큰이 없거나 올바르지 않습니다."}, status_code=401)
        diag = getattr(app.state.service, "diagnostics", None) if path.startswith("/api/") and not path.endswith("/stream") else None
        if diag is not None:
            diag.request_started()
            t0 = time.perf_counter()
            status = 500
            try:
                resp = await call_next(request)
                status = resp.status_code
            finally:
                route = request.scope.get("route")
                # the route's template only ("/api/stocks/{ticker}"): never a ticker, a query or a body
                diag.request(f"{request.method} {getattr(route, 'path', '(없는 경로)')}", time.perf_counter() - t0, status)
        else:
            resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        if remote and path.startswith("/api/"):
            resp.headers.setdefault("Cache-Control", "no-store")  # account figures are never kept by the phone's browser
        return resp

    app.add_middleware(CORSMiddleware, allow_origins=ALLOWED_ORIGINS, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["content-type", CLIENT_HEADER, TOKEN_HEADER])
    app.include_router(phone_router, prefix="/api")
    app.include_router(router, prefix="/api")
    app.include_router(habits_router, prefix="/api")
    app.include_router(quotes_router, prefix="/api")

    @app.get("/api/health/live", include_in_schema=False)
    def live() -> dict[str, Any]:
        return {"live": True, "version": __version__}

    @app.get("/api/health/ready", include_in_schema=False)
    def ready() -> JSONResponse:
        ok = bool(app.state.ready and app.state.service is not None)
        body = {"ready": ok, "version": __version__, "mode": app.state.service.mode.value if ok else None}
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.exception_handler(KeyError)
    async def not_found(_req: Any, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": f"찾을 수 없음: {exc}"}, status_code=404)

    @app.exception_handler(Exception)
    async def unexpected(_req: Any, exc: Exception) -> JSONResponse:
        log.exception("unhandled API error", exc_info=exc)
        return JSONResponse({"detail": "서버 내부 오류가 발생했습니다. 로그를 확인하세요."}, status_code=500)

    dist = dist_dir or Path(REPO_ROOT / "frontend" / "dist")
    if dist.exists():
        index = dist / "index.html"

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> Response:
            if path.startswith("api/"):
                return JSONResponse({"detail": "찾을 수 없음"}, status_code=404)
            f = safe_static_path(dist, path)
            if f is not None:
                return FileResponse(f)
            if not path or ("." not in path.rsplit("/", 1)[-1] and safe_static_path(dist, "index.html") is not None and not _BAD_PATH.search(path) and "%" not in path):
                return FileResponse(index)  # client-side route → the SPA shell, never another file
            return JSONResponse({"detail": "찾을 수 없음"}, status_code=404)

    return app


def _start_phone(app: FastAPI) -> None:
    """The phone link over the running service; its home-network listener starts when it is switched on."""
    from marketlens.api.phone_server import PhoneServer
    from marketlens.application.phone import PhoneLink

    svc = app.state.service
    try:
        link = PhoneLink(svc.sf, svc.now)
    except Exception as e:  # noqa: BLE001 - no settings table yet (a first start before migrations): no phone link
        log.info("phone link unavailable: %s", type(e).__name__)
        return
    link.server = PhoneServer(app, link.port)
    app.state.phone = link
    if link.enabled and not os.environ.get("MARKETLENS_NO_PHONE_LISTENER"):
        link.server.start()


def _warm(svc: Any) -> None:
    """LIVE start-up: the readiness counts, the macro snapshot and the calendar start loading in the background, so the
    first home screen has them or shows them as loading — it never waits for them."""
    try:
        svc.readiness()
    except Exception:  # noqa: BLE001 - a warm-up only; the first request computes it again
        logging.getLogger("marketlens.api").warning("readiness warm-up failed", exc_info=True)
    for name in ("macro_view", "calendar_view"):
        try:
            getattr(svc, name)()
        except Exception:  # noqa: BLE001 - a warm-up only
            logging.getLogger("marketlens.api").warning("%s warm-up failed", name, exc_info=True)
