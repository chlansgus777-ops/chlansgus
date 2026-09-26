"""Shared HTTP helper for live providers (httpx, typed errors, no secret logging)."""

from __future__ import annotations

import re

from typing import Any

import httpx

from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import ProviderDataError, ProviderUnavailable, RateLimited


class HttpClient:
    def __init__(self, base_url: str, headers: dict[str, str] | None = None, timeout: float = 15.0, bucket: TokenBucket | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self._client = httpx.Client(base_url=base_url, headers=headers or {}, timeout=timeout, transport=transport)
        self._bucket = bucket

    def get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        return self._request(path, params).text

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        r = self._request(path, params)
        try:
            return r.json()
        except ValueError as e:
            raise ProviderDataError("invalid JSON payload") from e

    def post_json(self, path: str, body: Any, headers: dict[str, str] | None = None) -> Any:
        if self._bucket is not None:
            self._bucket.acquire()
        try:
            r = self._client.post(path, json=body, headers=headers)
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"http error: {type(e).__name__}") from e
        self._check(r)
        try:
            return r.json()
        except ValueError as e:
            raise ProviderDataError("invalid JSON payload") from e

    def _request(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        if self._bucket is not None:
            self._bucket.acquire()
        try:
            r = self._client.get(path, params=params)
        except httpx.TimeoutException as e:
            raise ProviderUnavailable(f"timeout: {type(e).__name__}") from e
        except httpx.HTTPError as e:
            raise ProviderUnavailable(f"http error: {type(e).__name__}") from e
        self._check(r)
        return r

    @staticmethod
    def _detail(r: httpx.Response) -> str:
        """The provider's own error message (first 400 chars), with anything that looks like a credential removed —
        so a 400/403 says WHY (e.g. SEC "undeclared automated tool", a malformed filter) instead of only a code."""
        try:
            text = r.text
        except Exception:  # noqa: BLE001 - an undecodable body just gives no detail
            return ""
        text = re.sub(r"(?is)<(style|script)[^>]*>.*?</\1>", " ", text)
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"(?i)(api[_-]?key|apikey|token|secret|password|authorization)\s*[=:]\s*\S+", r"\1=<redacted>", text)
        text = re.sub(r"\b[A-Za-z0-9_\-]{32,}\b", "<redacted>", text)
        text = " ".join(text.split())
        return f": {text[:400]}" if text else ""

    @staticmethod
    def _check(r: httpx.Response) -> None:
        if r.status_code == 429:
            ra = r.headers.get("Retry-After")
            raise RateLimited("rate limited (429)", float(ra) if ra and ra.isdigit() else None)
        if r.status_code in (401, 403):
            raise ProviderUnavailable(f"unauthorized ({r.status_code}) — check API key / license / SEC User-Agent{HttpClient._detail(r)}")
        if r.status_code == 404:
            raise ProviderDataError("not found (404)")
        if r.status_code >= 500:
            raise ProviderUnavailable(f"server error {r.status_code}")
        if r.status_code >= 400:
            raise ProviderDataError(f"client error {r.status_code}{HttpClient._detail(r)}")
