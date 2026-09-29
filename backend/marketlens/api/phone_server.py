"""The home-network listener for the phone (application/phone.py): the same app on 0.0.0.0:<port>, started only while
the phone connection is switched on. The desktop listener stays on 127.0.0.1; api/app.py tells the two apart by the
client's address and lets a phone look, never change."""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger("marketlens.phone")


class PhoneServer:
    def __init__(self, app: Any, port: int) -> None:
        self.app, self.port = app, port
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    @property
    def listening(self) -> bool:
        return bool(self._server is not None and getattr(self._server, "started", False) and self._thread and self._thread.is_alive())

    def start(self) -> None:
        import uvicorn

        if self._thread is not None and self._thread.is_alive():
            return
        self.error = None
        # lifespan off: the app is already running (the desktop listener started its services)
        cfg = uvicorn.Config(self.app, host="0.0.0.0", port=self.port, log_level="warning", lifespan="off", access_log=False)  # noqa: S104
        self._server = uvicorn.Server(cfg)
        self._thread = threading.Thread(target=self._run, daemon=True, name="phone-server")
        self._thread.start()
        for _ in range(50):  # up to 5 s for the socket
            if self._server.started or not self._thread.is_alive():
                break
            time.sleep(0.1)
        if not self._server.started:
            self.error = self.error or f"포트 {self.port}을(를) 열지 못했습니다 — 다른 프로그램이 쓰고 있는지 확인하세요"

    def _run(self) -> None:
        try:
            self._server.run()
        except (OSError, SystemExit) as e:  # a busy port: uvicorn logs it and exits
            self.error = f"포트 {self.port}을(를) 열지 못했습니다 ({type(e).__name__})"
            log.warning("phone listener: %s", type(e).__name__)

    def stop(self) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server, self._thread = None, None
