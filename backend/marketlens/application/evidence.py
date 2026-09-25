"""Evidence registry: every fact that an agent or a UI explanation may cite has a stable ID."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Evidence:
    evidence_id: str
    category: str  # price | fundamental | valuation | earnings | analyst | macro | technical | issue | catalyst | options | risk | entry | portfolio | ownership
    label: str
    value: float | str | None
    source: str
    source_ts: datetime | None
    quality: str


def evidence_id(key: str, ticker: str | None, as_of: datetime) -> str:
    """e.g. ('analyst.eps_revision_30d', 'NVDA') → ANALYST_EPS_REVISION_30D_NVDA_20260925."""
    base = key.upper().replace(".", "_").replace("/", "_").replace(" ", "_")
    stamp = as_of.strftime("%Y%m%d")
    return f"{base}_{ticker}_{stamp}" if ticker else f"{base}_{stamp}"


class EvidenceBuilder:
    def __init__(self, ticker: str, as_of: datetime) -> None:
        self.ticker = ticker
        self.as_of = as_of
        self._items: dict[str, Evidence] = {}
        self._key_to_id: dict[str, str] = {}

    def add(self, key: str, category: str, label: str, value: float | str | None, source: str, source_ts: datetime | None = None, quality: str = "FRESH", ticker_scoped: bool = True, explicit_id: str | None = None) -> str:
        eid = explicit_id or evidence_id(key, self.ticker if ticker_scoped else None, self.as_of)
        if isinstance(value, float):
            value = round(value, 6)
        self._items[eid] = Evidence(eid, category, label, value, source, source_ts, quality)
        self._key_to_id[key] = eid
        return eid

    def id_for(self, key: str) -> str | None:
        return self._key_to_id.get(key)

    def ids_for(self, keys: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(i for k in keys if (i := self._key_to_id.get(k)) is not None)

    def items(self) -> tuple[Evidence, ...]:
        return tuple(self._items.values())
