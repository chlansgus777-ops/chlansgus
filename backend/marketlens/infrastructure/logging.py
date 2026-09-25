"""Structured JSON logging with event names and secret redaction."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from pathlib import Path
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
    re.compile(r"((?<![A-Za-z0-9])(?:api_key|apikey|api-key|token|access_token|key|secret|client_secret|password)=)([^&\s\"']+)", re.IGNORECASE),
    re.compile(r"((?:authorization|x-api-key|x-marketlens-token|proxy-authorization)\s*[:=]\s*)((?:bearer|basic)\s+)?([^\s,;\"'}]+)", re.IGNORECASE),
    re.compile(r"(\bbearer\s+)([A-Za-z0-9._\-~+/=]{8,})", re.IGNORECASE),
]
# exact sensitive key names (or *_api_key / *_secret / *_token); counters such as "input_tokens" are not secrets
SENSITIVE_KEYS = re.compile(r"^(.*[_-])?(authorization|api[_-]?key|apikey|token|secret|password|passwd|credentials?|cookie)$", re.IGNORECASE)
REDACTED = "***REDACTED***"


class SecretRedactor(logging.Filter):
    """Redacts configured secrets and secret-shaped values from messages, structured fields (recursively,
    including by sensitive key name), exception tracebacks and third-party library records."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def redact(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, REDACTED)
        for p in _KEY_PATTERNS:
            def sub(m: re.Match[str]) -> str:
                if m.lastindex is None or m.lastindex == 1:
                    return REDACTED
                return "".join(g for g in m.groups()[:-1] if g) + REDACTED
            text = p.sub(sub, text)
        return text

    def redact_obj(self, obj: Any, depth: int = 0) -> Any:
        if depth > 8:
            return "…"
        if isinstance(obj, str):
            return self.redact(obj)
        if isinstance(obj, dict):
            return {k: (REDACTED if isinstance(k, str) and SENSITIVE_KEYS.search(k) and v not in (None, "", False) and not isinstance(v, bool) else self.redact_obj(v, depth + 1)) for k, v in obj.items()}
        if isinstance(obj, (list, tuple, set)):
            return [self.redact_obj(v, depth + 1) for v in obj]
        return obj

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except (TypeError, ValueError):
            msg = str(record.msg)
        record.msg = self.redact(msg)
        record.args = ()
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            record.fields = self.redact_obj(fields)
        if record.exc_info and not getattr(record, "_ml_exc_redacted", False):
            record.exc_text = self.redact(logging.Formatter().formatException(record.exc_info))
            record._ml_exc_redacted = True  # type: ignore[attr-defined]
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
        if record.exc_text:
            payload["exc"] = record.exc_text
        return json.dumps(payload, default=str, ensure_ascii=False)


NOISY_LOGGERS = ("httpx", "httpcore", "anthropic", "urllib3")


def configure_logging(level: str = "INFO", secrets: Iterable[str] = (), log_dir: Path | None = None) -> None:
    """JSON logs to stderr (and a rotating file when ``log_dir`` is given). The redactor sits on every
    handler, so records from third-party libraries (e.g. httpx request URLs with ?token=) are redacted too."""
    redactor = SecretRedactor(secrets)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_dir is not None:
        from logging.handlers import RotatingFileHandler

        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(RotatingFileHandler(log_dir / "marketlens.log", maxBytes=5_000_000, backupCount=5, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(JsonFormatter())
        h.addFilter(redactor)
    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_marketlens", False):
            root.removeHandler(h)
    for h in handlers:
        h._marketlens = True  # type: ignore[attr-defined]
        root.addHandler(h)
    root.setLevel(logging.WARNING)
    ml = logging.getLogger("marketlens")
    ml.setLevel(level)
    for h in list(ml.handlers):
        ml.removeHandler(h)
    ml.propagate = True
    for name in NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def log_event(logger: logging.Logger, event: Event, msg: str = "", level: int = logging.INFO, **fields: Any) -> None:
    logger.log(level, msg or event.value, extra={"event": event, "fields": fields})
