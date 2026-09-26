"""FINRA consolidated short interest (FINRA Query API; published twice a month).

Data:  POST https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest with a JSON filter.
Auth:  FINRA API uses OAuth 2.0 client credentials. With FINRA_API_KEY / FINRA_API_SECRET (free API
       console account) an access token is requested from the FINRA Identity Platform with HTTP Basic
       credentials, and the data request carries ``Authorization: Bearer <token>`` — the client
       credentials are never sent to the data endpoint. Without credentials the public (rate-limited)
       access is used.
Coverage: the dataset is published under FINRA's "otcMarket" group. Whether it covers a given
       exchange-listed symbol must be confirmed by the live smoke test (NVDA/AAPL rows present); until
       then this provider is IMPLEMENTED_NOT_LIVE_VERIFIED and a symbol without rows is MISSING.
"""

from __future__ import annotations

import base64
import time
from datetime import date
from typing import Any, Callable

from marketlens.domain.enums import DataMode
from marketlens.domain.options import OwnershipSnapshot
from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import NotSupported, ProviderDataError
from marketlens.providers.live.http import HttpClient

DATASET = "/data/group/otcMarket/name/consolidatedShortInterest"
TOKEN_URL = "https://ews.fip.finra.org"
TOKEN_PATH = "/fip/rest/ews/oauth2/access_token"


class FinraShortInterestProvider:
    mode = DataMode.LIVE

    def __init__(self, api_key: str | None = None, api_secret: str | None = None, transport: Any = None, rate_per_s: float = 1.0,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.name = "finra"
        self.configured = True  # public dataset; credentials only raise limits
        self._basic = "Basic " + base64.b64encode(f"{api_key}:{api_secret}".encode()).decode() if api_key and api_secret else None
        self._http = HttpClient("https://api.finra.org", headers={"Accept": "application/json"}, bucket=TokenBucket(rate_per_s, 3), transport=transport)
        self._auth = HttpClient(TOKEN_URL, headers={"Accept": "application/json"}, transport=transport)
        self._token: tuple[str, float] | None = None
        self._clock = clock

    def _bearer(self) -> dict[str, str] | None:
        """OAuth2 client-credentials token (cached until shortly before it expires)."""
        if self._basic is None:
            return None
        now = self._clock()
        if self._token is None or now >= self._token[1]:
            d = self._auth.post_json(f"{TOKEN_PATH}?grant_type=client_credentials", None, headers={"Authorization": self._basic})
            tok = d.get("access_token") if isinstance(d, dict) else None
            if not tok:
                raise ProviderDataError("FINRA: access token missing in OAuth response")
            ttl = float(d.get("expires_in", 1800) or 1800)
            self._token = (str(tok), now + max(60.0, ttl - 60.0))
        return {"Authorization": f"Bearer {self._token[0]}"}

    def get_short_interest(self, ticker: str, today: date | None = None) -> OwnershipSnapshot:
        # Real FINRA contract (first live run, 2026-09-26): "Sorting is allowed only if all partition keys are specified
        # in EQUAL CompareFilter … missing: settlementDate". So no server-side sort: the last ~100 days of settlement
        # dates are requested with a date-range filter and ordered here.
        end = today or date.today()
        body = {
            "limit": 12,
            "compareFilters": [{"compareType": "EQUAL", "fieldName": "symbolCode", "fieldValue": ticker.upper()}],
            "dateRangeFilters": [{"fieldName": "settlementDate", "startDate": date.fromordinal(end.toordinal() - 100).isoformat(), "endDate": end.isoformat()}],
        }
        rows = self._http.post_json(DATASET, body, headers=self._bearer())
        if not isinstance(rows, list):
            raise ProviderDataError("FINRA: malformed payload")
        rows = sorted((r for r in rows if isinstance(r, dict) and r.get("settlementDate")), key=lambda r: str(r["settlementDate"]), reverse=True)
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
