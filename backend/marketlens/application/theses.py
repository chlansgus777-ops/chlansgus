"""Thesis invalidation conditions loader."""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from marketlens.config import CONFIG_DIR
from marketlens.domain.entry import ThesisCondition


def _cond(d: dict[str, Any]) -> ThesisCondition:
    return ThesisCondition(
        condition_id=d["id"],
        description=d["description"],
        metric=d.get("metric"),
        operator=d.get("operator"),
        threshold=float(d["threshold"]) if "threshold" in d else None,
        issue_category=d.get("issue_category"),
        issue_threshold=float(d["issue_threshold"]) if "issue_threshold" in d else None,
    )


class ThesisBook:
    def __init__(self, path: Path | None = None) -> None:
        d = tomllib.loads((path or CONFIG_DIR / "theses.toml").read_text(encoding="utf-8"))
        self.version = d["version"]
        self._default = [_cond(x) for x in d.get("default", [])]
        self._ticker = {t: [_cond(x) for x in items] for t, items in d.get("ticker", {}).items()}

    def for_ticker(self, ticker: str) -> tuple[ThesisCondition, ...]:
        return tuple(self._ticker.get(ticker, []) + self._default)
