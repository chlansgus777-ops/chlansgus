"""Alpha Vantage EARNINGS_ESTIMATES (free key; ~25 requests/day on the free tier — respect it).

GET https://www.alphavantage.co/query?function=EARNINGS_ESTIMATES&symbol=IBM&apikey=KEY

Per horizon ("current fiscal year", "next fiscal year", "current fiscal quarter", "next fiscal quarter"):
consensus EPS (avg/high/low), analyst count, the consensus 7/30/60/90 days ago, up/down revision counts,
and revenue consensus. The field names below follow the provider documentation; they are checked on
every response (``contract_issues``) and the live smoke test reports any mismatch — until a real response
has been seen this adapter is IMPLEMENTED_NOT_LIVE_VERIFIED.

The daily budget is enforced by the caller (only the final top-N candidates are requested, results are
cached for days). Rate-limit notices ("Note"/"Information") are RateLimited, never parsed as data.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from marketlens.domain.enums import DataMode
from marketlens.domain.estimates import EstimateObservation
from marketlens.infrastructure.resilience import TokenBucket
from marketlens.providers.contracts import NotSupported, ProviderDataError, ProviderUnavailable, RateLimited
from marketlens.providers.live.http import HttpClient

HORIZONS = {
    "current fiscal year": ("annual", "FY1"),
    "next fiscal year": ("annual", "FY2"),
    "current fiscal quarter": ("quarter", "FQ1"),
    "next fiscal quarter": ("quarter", "FQ2"),
}
EXPECTED_FIELDS = ("date", "horizon", "eps_estimate_average", "eps_estimate_analyst_count", "eps_estimate_average_30_days_ago", "revenue_estimate_average")


def _f(v: Any) -> float | None:
    try:
        if v is None or str(v).strip().lower() in ("", "none", "null", "-", "n/a"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_estimates(payload: Any, ticker: str, observed_on: date) -> tuple[list[EstimateObservation], list[str]]:
    """Returns (observations, contract_issues). Unknown horizons are skipped, never guessed."""
    if not isinstance(payload, dict):
        raise ProviderDataError("alphavantage: malformed payload")
    for k in ("Note", "Information"):
        if k in payload and "estimates" not in payload:
            raise RateLimited(f"alphavantage: {str(payload[k])[:120]}")
    if "Error Message" in payload:
        raise ProviderDataError(f"alphavantage: {str(payload['Error Message'])[:120]}")
    rows = payload.get("estimates")
    if not isinstance(rows, list):
        raise ProviderDataError("alphavantage: 'estimates' missing")
    if not rows:
        raise NotSupported(f"{ticker}: Alpha Vantage 추정치 없음")
    issues: list[str] = []
    out: list[EstimateObservation] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        missing = [f for f in EXPECTED_FIELDS if f not in r]
        if missing:
            issues.append(f"{r.get('horizon', '?')}: 필드 없음 {missing}")
        horizon = str(r.get("horizon", "")).strip().lower()
        if horizon not in HORIZONS:
            continue
        ptype, _slot = HORIZONS[horizon]
        try:
            pend = date.fromisoformat(str(r.get("date")))
        except ValueError:
            issues.append(f"{horizon}: 날짜 형식 오류 {r.get('date')!r}")
            continue
        cnt = _f(r.get("eps_estimate_analyst_count"))
        revs = {f"eps_{w}d_ago": _f(r.get(f"eps_estimate_average_{w}_days_ago")) for w in (7, 30, 60, 90)}
        for w in (7, 30):
            revs[f"up_{w}d"] = _f(r.get(f"eps_estimate_revision_up_trailing_{w}_days"))
            revs[f"down_{w}d"] = _f(r.get(f"eps_estimate_revision_down_trailing_{w}_days"))
        out.append(EstimateObservation(
            ticker=ticker, provider="alphavantage", period=f"{ptype}:{pend.isoformat()}", period_type=ptype, period_end=pend,
            observed_on=observed_on, eps=_f(r.get("eps_estimate_average")), revenue=_f(r.get("revenue_estimate_average")),
            analyst_count=int(cnt) if cnt is not None else None, eps_high=_f(r.get("eps_estimate_high")), eps_low=_f(r.get("eps_estimate_low")),
            horizon=horizon, provider_revisions=revs, provider_timestamp=datetime.now(tz=timezone.utc),
        ))
    return out, issues


class AlphaVantageEstimatesProvider:
    mode = DataMode.LIVE

    def __init__(self, api_key: str | None, transport: Any = None, rate_per_s: float = 0.2) -> None:
        self.name = "alphavantage"
        self.configured = bool(api_key)
        self._key = api_key
        self._http = HttpClient("https://www.alphavantage.co", bucket=TokenBucket(rate_per_s, 1), transport=transport)
        self.last_contract_issues: list[str] = []

    def get_estimate_observations(self, ticker: str, observed_on: date) -> list[EstimateObservation]:
        if not self.configured:
            raise ProviderUnavailable("ALPHAVANTAGE_API_KEY 미설정")
        d = self._http.get_json("/query", {"function": "EARNINGS_ESTIMATES", "symbol": ticker, "apikey": self._key})
        obs, self.last_contract_issues = parse_estimates(d, ticker, observed_on)
        return obs

    # the rest of the analyst contract is not offered here → the chain falls over / reports MISSING
    def get_estimates(self, ticker: str, as_of: date) -> Any:
        raise NotSupported("Alpha Vantage 추정치는 저장된 스냅샷으로만 사용(EstimateBook)")

    def get_earnings_history(self, ticker: str) -> Any:
        raise NotSupported("Alpha Vantage 실적 이력 미사용")

    def get_valuation_history(self, ticker: str, multiple: str) -> Any:
        raise NotSupported("과거 밸류에이션 시계열 미제공(MISSING)")
