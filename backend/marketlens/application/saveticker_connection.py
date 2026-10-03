"""Normal, opt-in SaveTicker login, with asynchronous feedback and no browser session export."""
from __future__ import annotations

import threading
import time
import secrets
import hmac
from typing import TYPE_CHECKING

from marketlens.config import save_setup, validate_setup
from marketlens.domain.enums import DataMode
from marketlens.infrastructure.resilience import RetryConfig
from marketlens.providers.router import ProviderChain
from marketlens.providers.saveticker import SaveTickerNewsProvider
from marketlens.providers.saveticker.browser import BrowserNewsProvider

if TYPE_CHECKING:
    from marketlens.application.services import MarketLensService


class SaveTickerConnection:
    def __init__(self, service: MarketLensService):
        self.service = service
        self._lock = threading.RLock()
        self._busy = self._closed = False
        self._retry_at = 0.0
        self._error: str | None = None
        self._saved = bool(service.settings.saveticker_enabled and service.settings.saveticker_email and service.settings.saveticker_password)
        self._browser_key: str | None = None
        self._browser_deadline = 0.0
        self._browser: BrowserNewsProvider | None = None
        self._browser_error: str | None = None

    def start_browser(self) -> dict:
        if self.service.mode != DataMode.LIVE:
            raise ValueError("실제 뉴스 수집은 LIVE 모드에서만 연결할 수 있습니다.")
        with self._lock:
            if self._busy or self._closed:
                raise ValueError("다른 연결 작업이 진행 중이거나 앱이 종료 중입니다.")
            if self._browser_key and time.monotonic() < self._browser_deadline:
                return {"key": self._browser_key, "expires_in_s": max(0, int(self._browser_deadline - time.monotonic()))}
            self._browser_key = secrets.token_hex(32)
            self._browser_deadline = time.monotonic() + 24 * 3600
            self._browser = BrowserNewsProvider(self.service.now)
            self._browser_error = self._error = None
            return {"key": self._browser_key, "expires_in_s": 24 * 3600}

    def browser_authorized(self, key: str | None) -> bool:
        with self._lock:
            return bool(not self._closed and key and self._browser_key and time.monotonic() < self._browser_deadline
                        and hmac.compare_digest(key, self._browser_key))

    def receive_browser(self, key: str, body: dict) -> dict:
        with self._lock:
            if not self.browser_authorized(key):
                raise PermissionError("브라우저 연결 키가 없거나 만료됐습니다.")
            if "resource" in body:
                result = self._browser.update_supplement(body["resource"], body)
                if body["resource"] == "details" and result["accepted"]:
                    self.service.refresher.invalidate("news-supplement")
                return result
            if "error" in body:
                messages = {
                    "AUTH_REQUIRED": "SaveTicker 탭에서 정상 로그인해주세요. MarketLens에 비밀번호를 다시 입력할 필요는 없습니다.",
                    "ACCESS_DENIED": "SaveTicker 브라우저 탭의 접근·보안 검증 상태를 확인하세요.",
                    "NO_TAB": "SaveTicker 탭을 열고 로그인해주세요. 브라우저가 열려 있어야 수집할 수 있습니다.",
                    "NETWORK": "브라우저 뉴스 수신 실패: 연결 복구 후 다시 수집합니다. 마지막 자료는 유지합니다.",
                    "FORMAT": "SaveTicker 뉴스 응답 형식이 바뀌었습니다. 기존 자료를 유지합니다.",
                }
                if not isinstance(body["error"], str) or body["error"] not in messages:
                    raise ValueError("알 수 없는 브라우저 수신 상태입니다.")
                self._browser_error = messages[body["error"]]
                return {"accepted": False}
            if not isinstance(body.get("news"), dict):
                raise ValueError("뉴스 응답이 없습니다.")
            browser = self._browser
            rows = browser.update(body["news"])
            previous = self.service.registry.extras.get("news_supplement")
            if not previous or previous.providers[0] is not browser:
                chain = ProviderChain("news", [browser], DataMode.LIVE, self.service.health,
                                      retry_cfg=RetryConfig(attempts=1))
                self.service.registry.extras["news_supplement"] = chain
            self.service.refresher.invalidate("news-supplement")
            self.service.refresher.get("news-supplement", browser.get_latest_news, max_age=120)
            self._browser_error = self._error = None
        if previous and previous.providers[0] is not browser:
            for old in previous.providers:
                self.service.refresher.get(f"saveticker-retire:{id(old)}", old.close, max_age=float("inf"))
        return {"accepted": True, "items_fetched": browser.items_fetched, "items_normalized": browser.items_normalized}

    def supplement_view(self) -> dict:
        with self._lock:
            return self._browser.supplement_view() if self._browser else {"enabled": False, "resources": {}}

    def stop_browser(self) -> dict:
        with self._lock:
            self._browser_key = None
            self._browser_deadline = 0.0
            self._browser_error = None
            if self._browser:
                self._browser.close()
            # Keep the last news with its real timestamp; provider freshness will flag it as old.
            return {"disconnected": True}

    def status(self) -> dict:
        with self._lock:
            if self._browser:
                at = self._browser.received_at
                age = (self.service.now() - at).total_seconds() if at else None
                disconnected = self._browser_key is None
                expired = time.monotonic() >= self._browser_deadline
                return {"status": "DISCONNECTED" if disconnected else "FAILED" if self._browser_error else "STALE" if expired or age is not None and age > 300 else
                        "CONNECTED" if at else "WAITING_BROWSER", "method": "browser", "enabled": at is not None,
                        "authenticated": False, "collection_confirmed": at is not None, "saved": False,
                        "error": "브라우저 연결을 해제했습니다. 마지막 자료는 이전 값입니다." if disconnected else self._browser_error or ("브라우저 연결이 만료되거나 수신이 중단됐습니다. 마지막 자료는 이전 값입니다." if expired or age is not None and age > 300 else None),
                        "retry_in_s": 0, "last_received": at.isoformat() if at else None,
                        "items_fetched": self._browser.items_fetched}
            chain = self.service.registry.extras.get("news_supplement")
            provider = chain.providers[0] if chain else None
            source_error = self.service.refresher.peek("news-supplement").error
            return {"status": "CONNECTING" if self._busy else "FAILED" if self._error or source_error else
                    "CONNECTED" if provider and provider.authenticated else "IDLE",
                    "enabled": bool(chain), "authenticated": bool(provider and provider.authenticated),
                    "error": self._error or source_error, "saved": self._saved,
                    "retry_in_s": max(0, round(self._retry_at - time.monotonic())),
                    "items_fetched": provider.items_fetched if provider else 0}

    def connect(self, email: str, password: str, remember: bool = False) -> dict:
        if self.service.mode != DataMode.LIVE:
            raise ValueError("SaveTicker 실제 수집은 LIVE 모드에서만 연결할 수 있습니다.")
        clean = validate_setup({"SAVETICKER_EMAIL": email, "SAVETICKER_PASSWORD": password})
        if len(clean) != 2:
            raise ValueError("이메일과 비밀번호를 함께 입력하세요.")
        with self._lock:
            if self._closed:
                raise ValueError("앱이 종료 중입니다.")
            if self._browser_key:
                raise ValueError("브라우저 연결을 해제한 뒤 직접 로그인을 시작하세요.")
            if self._busy or time.monotonic() < self._retry_at:
                return self.status() | {"started": False}
            self._busy, self._error = True, None
            self._browser = None
            self.service.refresher.invalidate("saveticker-connect")

            def run() -> bool:
                provider = SaveTickerNewsProvider(email=clean["SAVETICKER_EMAIL"], password=clean["SAVETICKER_PASSWORD"])
                try:
                    chain = ProviderChain("news", [provider], DataMode.LIVE, self.service.health,
                                          retry_cfg=RetryConfig(attempts=1))
                    rows = chain.call("get_latest_news").value
                    with self._lock:
                        if self._closed:
                            return False
                        if remember:
                            save_setup(clean | {"SAVETICKER_ENABLED": "1"})
                        previous = self.service.registry.extras.get("news_supplement")
                        self.service.registry.extras["news_supplement"] = chain
                        self.service.refresher.invalidate("news-supplement")
                        self.service.refresher.get("news-supplement", lambda: rows, max_age=120)
                        self._saved = remember
                    if previous:
                        for old in previous.providers:
                            old.close()
                    return True
                except Exception as ex:
                    with self._lock:
                        detail = str(ex)
                        reason = "HTTP 403 접근 거절" if "403" in detail else "HTTP 401 인증 거절" if "401" in detail else "HTTP 429 요청 제한" if "429" in detail else "뉴스 형식·발행 시각 검증 실패" if "SaveTickerParseError" in detail else "응답·연결 오류"
                        self._error = ("SaveTicker 브라우저 보안 검증이 서버 요청을 차단했습니다 (HTTP 403). 브라우저 로그인은 서버에 전달되지 않습니다. 비밀번호 오류로 확인된 상황은 아니며, 직접 HTTP 연결로 수집하지 못했습니다."
                                       if "브라우저 보안 검증" in detail else f"SaveTicker 로그인 또는 뉴스 수집 실패 ({reason}). 계정 정보·서비스 접근 상태를 확인하세요.")
                        self._retry_at = time.monotonic() + 30
                    provider.close()
                    return False
                finally:
                    with self._lock:
                        if self._closed:
                            provider.close()
                        self._busy = False

            self.service.refresher.get("saveticker-connect", run, max_age=0)
            return self.status() | {"started": True}

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._browser_key = None
