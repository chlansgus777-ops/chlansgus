"""All SaveTicker URLs and payload knowledge stay in this provider package."""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Callable

import httpx

from marketlens.domain.enums import DataMode
from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import NewsItem, ProviderUnavailable, ProviderDataError, ProviderError
from marketlens.providers.live.http import HttpClient
from .parser import parse_news

log = logging.getLogger(__name__)


class SaveTickerNewsProvider:
    name = "saveticker"
    mode = DataMode.LIVE
    configured = True

    def __init__(self, transport: httpx.BaseTransport | None = None, now: Callable[[], datetime] | None = None,
                 *, email: str | None = None, password: str | None = None, rate_per_s: float = 0.5):
        self.http = HttpClient("https://saveticker.com", timeout=6, transport=transport,
                               bucket=TokenBucket(rate_per_s=rate_per_s, capacity=1), headers={"Accept": "application/json"})
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.items_fetched = self.items_normalized = 0
        self._email, self._password = email, password
        self._authenticated = False
        self._login_failed = False
        self._session_lock = threading.RLock()

    @property
    def authenticated(self) -> bool:
        return self._authenticated

    def _login(self) -> None:
        if self._login_failed:
            raise ProviderDataError("SaveTicker 로그인 실패: 설정에서 계정을 다시 확인하세요. 자동 로그인 재시도는 중지했습니다.")
        if not self._email or not self._password:
            raise ProviderDataError("SaveTicker 이메일과 비밀번호를 함께 입력하세요.")
        try:
            # The site's own email login endpoint. httpx retains ONLY cookies issued to this client;
            # no browser session, token export, or persistent cookie file is involved.
            result = self.http.post_json("/api/auth/login", {"email": self._email, "password": self._password})
            if not isinstance(result, dict) or not isinstance(result.get("user_info"), dict):
                raise ProviderDataError("unexpected login response")
        except ProviderError as ex:
            self._login_failed = True
            self._authenticated = False
            # Provider bodies may echo credentials: never expose the response or exception chain.
            if "(403)" in str(ex) and "Just a moment" in str(ex):
                raise ProviderDataError("SaveTicker 브라우저 보안 검증이 서버 로그인 요청을 차단했습니다 (HTTP 403). 계정 정보 오류는 확인되지 않았으며 비밀번호 재입력만으로 해결되지 않을 수 있습니다.") from None
            reason = "HTTP 403 접근 거절" if "(403)" in str(ex) else "HTTP 401 인증 거절" if "(401)" in str(ex) else "HTTP 429 요청 제한" if "429" in str(ex) else "응답·연결 오류"
            raise ProviderDataError(f"SaveTicker 정상 로그인 실패 ({reason}): 계정 정보·서비스 접근 상태를 확인하세요.") from None
        self._authenticated = True

    def get_latest_news(self) -> list[NewsItem]:
        with self._session_lock:
            return self._get_latest_news()

    def _get_latest_news(self) -> list[NewsItem]:
        if (self._email or self._password) and not self._authenticated:
            self._login()
        try:
            payload = self.http.get_json("/api/news/list", {"page": 1, "page_size": 20, "sort": "created_at_desc"})
        except ProviderUnavailable as ex:
            if "(401)" in str(ex) and self._authenticated:
                self._authenticated = False
                self._login()  # one normal re-login for this expired client session
                try:
                    payload = self.http.get_json("/api/news/list", {"page": 1, "page_size": 20, "sort": "created_at_desc"})
                except (ProviderUnavailable, ProviderDataError):
                    self._authenticated = False
                    self._login_failed = True
                    raise ProviderDataError("SaveTicker 세션 복구 후에도 뉴스 조회가 실패했습니다. 설정에서 연결을 확인하세요.") from None
            elif "(401)" in str(ex) or "(403)" in str(ex):
                raise ProviderDataError("SaveTicker 뉴스 접근 거절: 정상 로그인 또는 서비스 접근 권한을 확인하세요.") from None
            else:
                raise
        # SaveTicker's public serverTimeToUserDayjs explicitly interprets server time in Asia/Seoul.
        # Offsets in actual payloads win; missing offsets use this source-specific documented policy.
        rows = parse_news(payload, self.now(), timezone(timedelta(hours=9)))
        self.items_fetched = len(payload["news_list"])
        self.items_normalized = len(rows)
        log.info("SAVETICKER_NEWS fetched=%s normalized=%s", self.items_fetched, self.items_normalized)
        return rows

    def close(self) -> None:
        with self._session_lock:
            self.http.close()
            self._email = self._password = None
            self._authenticated = False
