"""News received by the user's normal browser. No cookies, passwords or source session export."""
from __future__ import annotations

import json
import logging
import threading
import copy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from marketlens.domain.enums import DataMode
from marketlens.providers.contracts import NewsItem, NewsMetadata, ProviderUnavailable, ProviderDataError
from .parser import parse_news, classify_title
from .supplement import PARSERS


log = logging.getLogger("marketlens.saveticker")
OPTIONS_KEEP_S = 3 * 24 * 3600  # an option aggregate not read again for three days is dropped from the screens


class BrowserNewsProvider:
    name = "saveticker"
    mode = DataMode.LIVE
    configured = True
    authenticated = False  # collecting a news response does not prove the user's identity
    transport_mode = "browser"

    def __init__(self, now, history_dir: Path | None = None):
        self.now = now
        self._lock = threading.Lock()
        self._rows: list[NewsItem] = []
        self.received_at: datetime | None = None
        self.items_fetched = self.items_normalized = 0
        self._closed = False
        self._supplement = {name: {"rows": [], "last_success": None, "error": None} for name in PARSERS}
        # option aggregates by symbol: each read brings the few names MarketLens asked for, the screens show them all
        self._options: dict[str, dict] = {}
        # dated copies of what was received, for measuring later whether it helps the return estimates (owner
        # 2026-10-03: "수익계산에 도움이 된다면 계산법에 포함") — nothing is scored from it before that
        self.history_dir = history_dir
        self._recorded: dict[str, set[str]] = {}

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
            received = len(rows)
            if resource == "options":
                self._record("options", rows, lambda r: f"{r['related_companies'][0]['ticker']}|{r['dates'].get('snapshotDate') or r['dates'].get('batchDate')}")
                for r in rows:
                    self._options[r["related_companies"][0]["ticker"]] = r
                cutoff = self.now() - timedelta(seconds=OPTIONS_KEEP_S)
                self._options = {k: v for k, v in self._options.items() if datetime.fromisoformat(v["collected_at"]) >= cutoff}
                rows = list(self._options.values())
            elif resource == "details":
                self._record("earnings", [r for r in rows if r.get("earnings")], lambda r: str(r["news_id"]))
            self._supplement[resource] = {"rows": rows, "last_success": self.now().isoformat(), "error": None}
            return {"accepted": True, "resource": resource, "items_normalized": received}

    def options_read_within(self, seconds: float) -> set[str]:
        """The symbols whose option aggregate was received less than ``seconds`` ago."""
        with self._lock:
            now = self.now()
            return {k for k, v in self._options.items() if (now - datetime.fromisoformat(v["collected_at"])).total_seconds() < seconds}

    def _record(self, kind: str, rows: list[dict], key) -> None:  # noqa: ANN001
        """Append each new row to ``<history_dir>/<kind>_history.jsonl`` (its own key once per file). A failed write is
        logged and collection goes on: the screens never depend on it."""
        if self.history_dir is None or not rows:
            return
        path = self.history_dir / f"{kind}_history.jsonl"
        try:
            seen = self._recorded.get(kind)
            if seen is None:
                seen = set()
                if path.exists():
                    for line in path.read_text(encoding="utf-8").splitlines():
                        try:
                            seen.add(json.loads(line)["key"])
                        except (ValueError, KeyError, TypeError):
                            log.warning("saveticker history %s: an unreadable line kept as is", kind)
                self._recorded[kind] = seen
            new = [r for r in rows if key(r) not in seen]
            if not new:
                return
            self.history_dir.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                for r in new:
                    f.write(json.dumps({"key": key(r), "recorded_at": self.now().isoformat(), "row": r}, ensure_ascii=False, sort_keys=True) + "\n")
                    seen.add(key(r))
        except OSError as e:
            log.warning("saveticker history %s not written: %s", kind, type(e).__name__)

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
