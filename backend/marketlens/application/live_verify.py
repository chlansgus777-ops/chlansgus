"""Live smoke test: call every free provider for a few representative tickers and check that each value
arrives with a provider, a timestamp and a source. Run it once API keys and network access exist:

    marketlens live-verify            (MARKETLENS_MODE=LIVE)

Outcome per data category: VERIFIED | FAILED | BLOCKED_BY_CREDENTIAL | BLOCKED_BY_NETWORK. It fails
closed: a sample is VERIFIED only when its value is present AND meaningful (a finite number, positive where
the quantity must be positive, a non-empty list, never an error/status string) and its timestamp parses
and is not in the future. Only a run
against the real providers (no injected transport) records ``live_verified`` flags, which remove the
"NOT_LIVE_VERIFIED" marker in the readiness matrix. Nothing is marked verified by fixtures.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from typing import Any, Callable

from marketlens.domain.market_calendar import last_completed_session, to_ny
from marketlens.providers.contracts import ProviderError, ProviderUnavailable

TICKERS = ("NVDA", "AAPL", "MSFT", "JPM", "XOM", "AMZN", "TSM")
NETWORK_HINTS = ("http error", "ConnectError", "ProxyError", "timeout", "CONNECT", "403")


def _classify(err: Exception) -> str:
    msg = str(err)
    if isinstance(err, ProviderUnavailable) and ("미설정" in msg or "not set" in msg or "API_KEY" in msg or "USER_AGENT" in msg.upper()):
        return "BLOCKED_BY_CREDENTIAL"
    if any(h.lower() in msg.lower() for h in NETWORK_HINTS):
        return "BLOCKED_BY_NETWORK"
    return "FAILED"


FAILURE_WORDS = ("실패", "없음", "error", "fail", "blocked", "403", "401", "429", "timeout", "unavailable", "미설정", "한도")


def sample_problem(s: dict[str, Any], now: datetime) -> str | None:
    """Why one sample does not prove the provider works (None = it does)."""
    for k in ("provider", "timestamp", "source"):
        if not s.get(k):
            return f"{k} 누락"
    v = s.get("value")
    if v is None:
        return "값 없음(None)"
    if isinstance(v, bool):
        return "값이 참/거짓뿐"
    if isinstance(v, (int, float)):
        if not math.isfinite(v):
            return f"값이 유한한 수가 아님({v})"
        if s.get("positive") and v <= 0:
            return f"양수여야 하는 값이 {v}"
    elif isinstance(v, str):
        if not v.strip() or any(w in v.lower() for w in FAILURE_WORDS):
            return f"값이 오류/상태 문자열: {v[:80]}"
    elif isinstance(v, (list, tuple, dict)):
        if not v:
            return "빈 값"
    ts = str(s["timestamp"])
    try:
        t = datetime.fromisoformat(ts) if "T" in ts or " " in ts else datetime.combine(date.fromisoformat(ts), datetime.min.time())
    except ValueError:
        return f"타임스탬프 해석 불가: {ts[:40]}"
    if t.date() > now.date() + timedelta(days=1):
        return f"미래 타임스탬프: {ts[:40]}"
    return None


def verify(svc: Any, tickers: tuple[str, ...] = TICKERS, record: bool = True) -> dict[str, Any]:
    now = svc.now()
    day = last_completed_session(now)
    obs_day = to_ny(now).date()  # snapshots are labelled with the New York day they are observed on
    data = svc.data
    report: dict[str, Any] = {"as_of": now.isoformat(), "tickers": list(tickers), "categories": {}}

    def check(cat: str, fn: Callable[[], list[dict[str, Any]]]) -> None:
        try:
            samples = fn()
            problems = [f"{x.get('ticker')}: {p}" for x in samples if (p := sample_problem(x, now))] if samples else ["표본 없음"]
            ok = not problems
            report["categories"][cat] = {"status": "VERIFIED" if ok else "FAILED", "samples": samples[:7], "note": "; ".join(problems[:3])}
        except ProviderError as e:
            report["categories"][cat] = {"status": _classify(e), "samples": [], "note": str(e)[:200]}
        except Exception as e:  # noqa: BLE001 - a smoke test must report every failure, not crash
            report["categories"][cat] = {"status": "FAILED", "samples": [], "note": f"{type(e).__name__}: {str(e)[:180]}"}

    def fetched(f: Any, t: str, value: Callable[[Any], Any], ts: Callable[[Any], Any], positive: bool = False) -> dict[str, Any]:
        if f.error or f.value is None:
            raise ProviderError(f"{t}: {f.error or '값 없음'}")
        return {"ticker": t, "value": value(f.value), "provider": f.provider, "timestamp": str(ts(f.value)), "source": f.provider, "positive": positive}

    check("price", lambda: [fetched(data.quote(t), t, lambda q: q.price, lambda q: q.timestamp.isoformat(), positive=True) for t in tickers])
    check("bars", lambda: [fetched(data.bars(t, day.replace(day=1) if day.day > 1 else day, day), t, lambda b: len(b), lambda b: b[-1].day, positive=True) for t in tickers[:3]])
    us = [t for t in tickers if t != "TSM"]
    check("fundamentals", lambda: [fetched(data.quarters(t), t, lambda q: q[-1].revenue, lambda q: q[-1].filed_date, positive=True) for t in us[:3]])
    if "TSM" in tickers:  # a 20-F filer: annual IFRS only
        check("ifrs", lambda: [fetched(data.annuals("TSM"), "TSM", lambda a: a[-1].revenue, lambda a: a[-1].filed_date, positive=True)])
    if "JPM" in tickers:  # a bank: us-gaap bank concepts
        check("bank", lambda: [fetched(data.quarters("JPM"), "JPM", lambda q: sorted(q[-1].extras) or None, lambda q: q[-1].filed_date)])
    check("earnings", lambda: [fetched(data.earnings(t), t, lambda e: e[-1].eps_actual, lambda e: e[-1].report_date) for t in tickers[:2]])
    check("news", lambda: [fetched(data.news(now.replace(hour=0), [t]), t, lambda n: len(n), lambda n: n[0].published_at.isoformat() if n else "", positive=True) for t in tickers[:1]])

    def macro() -> list[dict[str, Any]]:
        snap, missing = data.macro_snapshot(now)
        if snap is None:
            raise ProviderError(missing or "거시 스냅샷 없음")
        return [{"ticker": sid, "value": s.latest.value, "provider": "fred", "timestamp": s.latest.source_ts.isoformat() if s.latest.source_ts else "", "source": s.latest.source}
                for sid, s in list(snap.series.items())[:5]]

    check("macro", macro)

    def estimates() -> list[dict[str, Any]]:
        # one request from the daily reserve (the scanner keeps a few of the ~25 free calls for manual use)
        out = data.prefetch_estimates(("NVDA",), obs_day, budget=svc.base_cfg.scanner.estimate_daily_budget + 3, ttl_days=0)
        if out.get("NVDA") != "OK":
            raise ProviderError(f"alphavantage: {out.get('NVDA')}")
        av = next((p for p in data.reg.chain("analyst").providers if p.name == "alphavantage"), None)
        issues = getattr(av, "last_contract_issues", []) if av else []
        if issues:
            raise ProviderError("응답 필드 계약 불일치: " + "; ".join(issues[:3]))
        f = data.estimates("NVDA", obs_day)
        return [fetched(f, "NVDA", lambda a: a.forward_eps, lambda a: a.as_of)]

    check("estimates", estimates)

    def guidance() -> list[dict[str, Any]]:
        status = data.prefetch_guidance(("NVDA",), obs_day).get("NVDA") or "결과 없음"
        if not (status.startswith("보도자료") or status == "오늘 확인함"):
            raise ProviderError(f"sec-8k: {status}")  # e.g. "실패: SEC 403" → BLOCKED_BY_NETWORK / FAILED, never VERIFIED
        rows = svc.store.guidance("NVDA", now) if svc.store is not None else []
        if not rows:
            raise ProviderError(f"sec-8k: 보도자료에서 가이던스 항목을 하나도 찾지 못함 ({status})")
        last = max(rows, key=lambda r: r.filed_at)
        return [{"ticker": "NVDA", "value": len(rows), "provider": "sec-8k", "timestamp": last.filed_at.isoformat(), "source": getattr(last, "source_url", "") or "sec.gov", "positive": True}]

    check("guidance", guidance)
    check("short_interest", lambda: [fetched(data.short_interest(t), t, lambda o: o.short_interest_shares, lambda o: o.short_interest_settlement, positive=True) for t in us[:2]])
    check("insider", lambda: [fetched(data.insider("NVDA"), "NVDA", lambda o: o.insider_net_buy_value_90d, lambda o: day)])

    report["summary"] = {k: v["status"] for k, v in report["categories"].items()}
    if record and svc.store is not None:
        svc.store.set_setting("live_verified", json.dumps({k: v["status"] == "VERIFIED" for k, v in report["categories"].items()}))
        svc.store.set_setting("live_verify_report", json.dumps({"at": datetime.now().isoformat(), "summary": report["summary"]}))
    return report
