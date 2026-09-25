"""FINRA consolidated short interest (free public data via the FINRA Query API; published twice a month).

POST https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest with a JSON filter.
Optional FINRA_API_KEY / FINRA_API_SECRET (free developer account) raise rate limits.
"""

from __future__ import annotations

import base64
from datetime import date
from typing import Any

from marketlens.domain.enums import DataMode
from marketlens.domain.options import OwnershipSnapshot
from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import NotSupported, ProviderDataError
from marketlens.providers.live.http import HttpClient

DATASET = "/data/group/otcMarket/name/consolidatedShortInterest"


class FinraShortInterestProvider:
    mode = DataMode.LIVE

    def __init__(self, api_key: str | None = None, api_secret: str | None = None, transport: Any = None, rate_per_s: float = 1.0) -> None:
        self.name = "finra"
        self.configured = True  # public dataset; credentials only raise limits
        headers = {"Accept": "application/json"}
        if api_key and api_secret:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{api_key}:{api_secret}".encode()).decode()
        self._http = HttpClient("https://api.finra.org", headers=headers, bucket=TokenBucket(rate_per_s, 3), transport=transport)

    def get_short_interest(self, ticker: str) -> OwnershipSnapshot:
        body = {
            "limit": 2,
            "compareFilters": [{"compareType": "equal", "fieldName": "symbolCode", "fieldValue": ticker.upper()}],
            "sortFields": ["-settlementDate"],
        }
        rows = self._http.post_json(DATASET, body)
        if not isinstance(rows, list):
            raise ProviderDataError("FINRA: malformed payload")
        if not rows:
            raise NotSupported(f"{ticker}: FINRA 공매도 데이터 없음")
        cur = rows[0]
        prev = rows[1] if len(rows) > 1 else None
        shares = _f(cur.get("currentShortPositionQuantity"))
        prev_shares = _f(prev.get("currentShortPositionQuantity")) if prev else _f(cur.get("previousShortPositionQuantity"))
        dtc = _f(cur.get("daysToCoverQuantity"))
        change = (shares / prev_shares - 1) if shares is not None and prev_shares else None
        return OwnershipSnapshot(
            source=f"finra:{cur.get('settlementDate', '')}",
            short_interest_shares=shares,
            days_to_cover=dtc,
            short_interest_change=round(change, 4) if change is not None else None,
            short_interest_settlement=date.fromisoformat(cur["settlementDate"]) if cur.get("settlementDate") else None,
        )


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
