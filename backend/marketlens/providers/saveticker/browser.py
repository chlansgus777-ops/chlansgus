"""News received by the user's normal browser. No cookies, passwords or source session export."""
from __future__ import annotations

import threading
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from marketlens.domain.enums import DataMode
from marketlens.providers.contracts import NewsItem, NewsMetadata, ProviderUnavailable, ProviderDataError
from .parser import parse_news, classify_title
from .supplement import PARSERS


class BrowserNewsProvider:
    name = "saveticker"
    mode = DataMode.LIVE
    configured = True
    authenticated = False  # collecting a news response does not prove the user's identity
    transport_mode = "browser"

    def __init__(self, now):
        self.now = now
        self._lock = threading.Lock()
        self._rows: list[NewsItem] = []
        self.received_at: datetime | None = None
        self.items_fetched = self.items_normalized = 0
        self._closed = False
        self._supplement = {name: {"rows": [], "last_success": None, "error": None} for name in PARSERS}

    def update_supplement(self, resource: str, body: dict) -> dict:
        if not isinstance(resource, str) or resource not in PARSERS:
            raise ValueError("unknown market resource")
        with self._lock:
            if self._closed:
                raise ValueError("browser disconnected")
            state = self._supplement[resource]
            if "error" in body:
                if not isinstance(body["error"], str) or body["error"] not in {"AUTH_REQUIRED", "ACCESS_DENIED", "NETWORK", "FORMAT"}:
                    raise ValueError("invalid source error")
                state["error"] = body["error"]
                return {"accepted": False, "resource": resource}
            try:
                rows = PARSERS[resource](body.get("payload"), self.now())
            except (ValueError, ProviderDataError):
                state["error"] = "FORMAT"
                raise
            self._supplement[resource] = {"rows": rows, "last_success": self.now().isoformat(), "error": None}
            return {"accepted": True, "resource": resource, "items_normalized": len(rows)}

    def supplement_view(self) -> dict:
        with self._lock:
            result = copy.deepcopy(self._supplement)
            for state in result.values():
                at = datetime.fromisoformat(state["last_success"]) if state["last_success"] else None
                state["stale"] = self._closed or at is None or (self.now() - at).total_seconds() > 900
            return {"enabled": True, "resources": result}

    def update(self, payload) -> list[NewsItem]:
        at = self.now()
        rows = parse_news(payload, at, timezone(timedelta(hours=9)))
        with self._lock:
            self._rows = rows
            self.received_at = at
            self.items_fetched = len(payload["news_list"])
            self.items_normalized = len(rows)
        return rows

    def get_latest_news(self) -> list[NewsItem]:
        with self._lock:
            if self._closed or self.received_at is None or (self.now() - self.received_at).total_seconds() > 300:
                raise ProviderUnavailable("SaveTicker 브라우저 수신이 중단됐습니다. 마지막 자료는 이전 수집 값입니다.")
            rows = {r.news_id: r for r in self._rows}
            detail_state = self._supplement["details"]
            detail_at = datetime.fromisoformat(detail_state["last_success"]) if detail_state["last_success"] else None
            if detail_at is None or (self.now() - detail_at).total_seconds() > 900:
                return list(rows.values())
            for detail in detail_state["rows"]:
                published = datetime.fromisoformat(detail["published_at"])
                if not 0 <= (self.now() - published).total_seconds() <= 48 * 3600:
                    continue
                key = detail["news_id"]
                item = rows.get(key)
                if item is None:
                    event, hint = classify_title(detail["title"])
                    # A detail has no reliable wire source unless a matching list record supplies it.
                    meta = NewsMetadata(provider=self.name, provider_url="https://saveticker.com/news",
                                        collected_at=detail_at, event_type=event, bullish_bearish_hint=hint)
                    item = NewsItem(key, published, detail["title"], "", "", "SaveTicker", "OTHER", (),
                                    metadata=meta, provenance=(self.name,))
                meta = replace(item.metadata, provider_summary=detail["provider_summary"], detail_collected_at=detail_at,
                               original_published_at=detail["original_published_at"] or item.metadata.original_published_at)
                related = tuple(sorted(set(item.tickers) | {c["ticker"] for c in detail["related_companies"]}))
                rows[key] = replace(item, tickers=related, body=detail["source_text"], metadata=meta)
            return list(rows.values())

    def close(self) -> None:
        with self._lock:
            self._closed = True
