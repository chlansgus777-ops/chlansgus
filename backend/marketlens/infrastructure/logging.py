"""Structured JSON logging with event names and secret redaction."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Iterable

from marketlens.domain.enums import StrEnum
from marketlens.domain.market_calendar import UTC


class Event(StrEnum):
    SCAN_STARTED = "SCAN_STARTED"
    SCAN_FINISHED = "SCAN_FINISHED"
    PROVIDER_FAILED = "PROVIDER_FAILED"
    PROVIDER_RECOVERED = "PROVIDER_RECOVERED"
    PROVIDER_CONFLICT = "PROVIDER_CONFLICT"
    ISSUE_CREATED = "ISSUE_CREATED"
    SCORE_CREATED = "SCORE_CREATED"
    DECISION_CREATED = "DECISION_CREATED"
    COMMITTEE_STARTED = "COMMITTEE_STARTED"
    COMMITTEE_FINISHED = "COMMITTEE_FINISHED"
    COMMITTEE_UNAVAILABLE = "COMMITTEE_UNAVAILABLE"
    AGENT_OUTPUT_REJECTED = "AGENT_OUTPUT_REJECTED"
    PROMPT_INJECTION_DETECTED = "PROMPT_INJECTION_DETECTED"
    RISK_VETO = "RISK_VETO"
    RECOMMENDATION_CHANGED = "RECOMMENDATION_CHANGED"
    PAPER_POSITION_OPENED = "PAPER_POSITION_OPENED"
    PAPER_POSITION_CLOSED = "PAPER_POSITION_CLOSED"
    CALIBRATION_RUN = "CALIBRATION_RUN"
    MODEL_PROMOTED = "MODEL_PROMOTED"
    OUTCOMES_UPDATED = "OUTCOMES_UPDATED"


_KEY_PATTERNS = [
    re.compile(r"(sk-ant-[A-Za-z0-9_\-]{8,})"),
    re.compile(r"(sk-[A-Za-z0-9_\-]{16,})"),
    re.compile(r"((?:api_key|apikey|token|key)=)([^&\s\"']+)", re.IGNORECASE),
]


class SecretRedactor(logging.Filter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = [s for s in secrets if s and len(s) >= 6]

    def redact(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***REDACTED***")
        for p in _KEY_PATTERNS:
            text = p.sub(lambda m: (m.group(1) + "***REDACTED***") if m.lastindex and m.lastindex > 1 else "***REDACTED***", text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self.redact(str(record.getMessage()))
        record.args = ()
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            record.fields = {k: (self.redact(v) if isinstance(v, str) else v) for k, v in fields.items()}
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.now(tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        ev = getattr(record, "event", None)
        if ev:
            payload["event"] = str(ev)
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", secrets: Iterable[str] = ()) -> None:
    root = logging.getLogger("marketlens")
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler.addFilter(SecretRedactor(secrets))
    root.addHandler(handler)
    root.propagate = False


def log_event(logger: logging.Logger, event: Event, msg: str = "", level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, msg or event.value, extra={"event": event, "fields": fields})
