"""FastAPI application. Thin HTTP layer: no business logic lives here."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from marketlens.api.routes import router
from marketlens.application.services import MarketLensService
from marketlens.config import REPO_ROOT, Settings, load_settings
from marketlens.infrastructure.db.session import make_engine, make_session_factory, migrate
from marketlens.infrastructure.logging import configure_logging

log = logging.getLogger("marketlens.api")


def create_app(settings: Settings | None = None, service: MarketLensService | None = None, run_migrations: bool = True) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level, settings.secrets())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.service is None:
            if run_migrations:
                migrate(settings.database_url)
            sf = make_session_factory(make_engine(settings.database_url))
            app.state.service = MarketLensService(settings, sf)
        sched = None
        if getattr(settings, "scheduler", False):
            from marketlens.workers.scheduler import BackgroundScheduler

            sched = BackgroundScheduler(app.state.service)
            sched.start()
        yield
        if sched:
            sched.stop()

    app = FastAPI(title="MarketLens", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "tauri://localhost", "http://tauri.localhost"], allow_methods=["*"], allow_headers=["*"])
    app.include_router(router, prefix="/api")

    @app.exception_handler(KeyError)
    async def not_found(_req: Any, exc: KeyError) -> JSONResponse:
        return JSONResponse({"detail": f"not found: {exc}"}, status_code=404)

    dist = Path(REPO_ROOT / "frontend" / "dist")
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            f = dist / path
            return FileResponse(f if f.is_file() else dist / "index.html")

    return app
