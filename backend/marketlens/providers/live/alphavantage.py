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
# the form the real API answers with (live-verify run 36253463681, NVDA: 41 rows): one row per fiscal period —
# past and future — whose horizon names only the period TYPE; "current/next" follows from the period-end date
PERIOD_TYPES = {"fiscal year": "annual", "fiscal quarter": "quarter"}
RECENT_DAYS = 120  # a period that ended up to ~4 months ago may still await its report: its consensus is kept
EXPECTED_FIELDS = ("date", "horizon", "eps_estimate_average", "eps_estimate_analyst_count", "eps_estimate_average_30_days_ago", "revenue_estimate_average")


def _f(v: Any) -> float | None:
    try:
        if v is None or str(v).strip().lower() in ("", "none", "null", "-", "n/a"):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_estimates(payload: Any, ticker: str, observed_on: date) -> tuple[list[EstimateObservation], list[str]]:
    """Returns (observations, contract_issues). Unknown horizons are skipped, never guessed. Accepts the documented
    horizon words ("current fiscal year" …) and the real API's per-period rows ("fiscal year" + period end): those
    are labelled current / next / +k by their date relative to ``observed_on``; long-past periods are dropped."""
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
    skipped: list[str] = []
    out: list[EstimateObservation] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        horizon = " ".join(str(r.get("horizon", "")).replace("_", " ").lower().split())
        if horizon not in HORIZONS and horizon not in PERIOD_TYPES:
            skipped.append(horizon or "?")
            continue
        ptype = HORIZONS[horizon][0] if horizon in HORIZONS else PERIOD_TYPES[horizon]
        try:
            pend = date.fromisoformat(str(r.get("date")))
        except ValueError:
            issues.append(f"{horizon}: 날짜 형식 오류 {r.get('date')!r}")
            continue
        if horizon in PERIOD_TYPES:
            if (observed_on - pend).days > RECENT_DAYS:
                continue  # a long-past period: history, not a current consensus (not a contract issue)
            ahead = sorted({date.fromisoformat(str(x.get("date"))) for x in rows if isinstance(x, dict) and _type_of(x) == horizon
                            and _iso(x.get("date")) and date.fromisoformat(str(x.get("date"))) >= observed_on})
            kind = horizon.split()[-1]
            horizon = (f"current fiscal {kind}" if pend == ahead[0] else f"next fiscal {kind}" if len(ahead) > 1 and pend == ahead[1]
                       else f"fiscal {kind} +{ahead.index(pend)}" if pend in ahead else f"recent fiscal {kind}") if ahead else f"recent fiscal {kind}"
        missing = [f for f in EXPECTED_FIELDS if f not in r]  # checked on the rows that are used
        if missing:
            issues.append(f"{r.get('horizon', '?')}: 필드 없음 {missing}")
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
    if skipped:
        issues.append(f"알 수 없는 horizon {len(skipped)}행 건너뜀: {sorted(set(skipped))[:4]}")
    return out, issues


def _type_of(r: Any) -> str:
    return " ".join(str(r.get("horizon", "")).replace("_", " ").lower().split())


def _iso(v: Any) -> bool:
    try:
        date.fromisoformat(str(v))
        return True
    except ValueError:
        return False


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
        if not obs:
            # every row skipped (horizon words other than the documented ones): an empty success would store
            # nothing and read back as "no estimates" — found by live-verify run 36252886492 ("OK", then MISSING)
            rows = d.get("estimates") or []
            keys = sorted(rows[0])[:12] if rows and isinstance(rows[0], dict) else []
            raise ProviderDataError(f"alphavantage: {ticker} {len(rows)}행 모두 해석 불가 — {'; '.join(self.last_contract_issues[:3])}; 기대 horizon {sorted(HORIZONS)}; 첫 행 키 {keys}")
        return obs

    # the rest of the analyst contract is not offered here → the chain falls over / reports MISSING
    def get_estimates(self, ticker: str, as_of: date) -> Any:
        raise NotSupported("Alpha Vantage 추정치는 저장된 스냅샷으로만 사용(EstimateBook)")

    def get_earnings_history(self, ticker: str) -> Any:
        raise NotSupported("Alpha Vantage 실적 이력 미사용")

    def get_valuation_history(self, ticker: str, multiple: str) -> Any:
        raise NotSupported("과거 밸류에이션 시계열 미제공(MISSING)")
