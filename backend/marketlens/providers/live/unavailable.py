"""Placeholder for provider kinds without a configured/licensed source.

It never returns data: every call raises ProviderUnavailable so the pipeline marks the data MISSING.
"""

from __future__ import annotations

from typing import Any

from marketlens.domain.enums import DataMode
from marketlens.providers.contracts import ProviderUnavailable


class UnavailableProvider:
    mode = DataMode.LIVE
    configured = False

    def __init__(self, kind: str, reason: str) -> None:
        self.name = f"unavailable-{kind}"
        self.kind = kind
        self.reason = reason

    def __getattr__(self, item: str) -> Any:
        if item.startswith("get_") or item.startswith("list_"):
            def _missing(*_a: Any, **_k: Any) -> Any:
                raise ProviderUnavailable(f"{self.kind}: {self.reason}")

            return _missing
        raise AttributeError(item)
