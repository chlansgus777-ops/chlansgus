"""Live smoke test: call every free provider for a few representative tickers and check that each value
arrives with a provider, a timestamp and a source. Run it once API keys and network access exist:

    marketlens live-verify            (MARKETLENS_MODE=LIVE)

Outcome per data category: VERIFIED | FAILED | BLOCKED_BY_CREDENTIAL | BLOCKED_BY_NETWORK. Only a run
against the real providers (no injected transport) records ``live_verified`` flags, which remove the
"NOT_LIVE_VERIFIED" marker in the readiness matrix. Nothing is marked verified by fixtures.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable

from marketlens.domain.market_calendar import last_completed_session
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


def verify(svc: Any, tickers: tuple[str, ...] = TICKERS, record: bool = True) -> dict[str, Any]:
    now = svc.now()
    day = last_completed_session(now)
    data = svc.data
    report: dict[str, Any] = {"as_of": now.isoformat(), "tickers": list(tickers), "categories": {}}

    def check(cat: str, fn: Callable[[], list[dict[str, Any]]]) -> None:
        try:
            samples = fn()
            ok = bool(samples) and all(s.get("provider") and s.get("timestamp") and s.get("source") for s in samples)
            report["categories"][cat] = {"status": "VERIFIED" if ok else "FAILED", "samples": samples[:7],
                                          "note": "" if ok else "값 또는 provider/timestamp/source 누락"}
        except ProviderError as e:
            report["categories"][cat] = {"status": _classify(e), "samples": [], "note": str(e)[:200]}
        except Exception as e:  # noqa: BLE001 - a smoke test must report every failure, not crash
            report["categories"][cat] = {"status": "FAILED", "samples": [], "note": f"{type(e).__name__}: {str(e)[:180]}"}

    def fetched(f: Any, t: str, value: Callable[[Any], Any], ts: Callable[[Any], Any]) -> dict[str, Any]:
        if f.error or f.value is None:
            raise ProviderError(f"{t}: {f.error or '값 없음'}")
        return {"ticker": t, "value": value(f.value), "provider": f.provider, "timestamp": str(ts(f.value)), "source": f.provider}

    check("price", lambda: [fetched(data.quote(t), t, lambda q: q.price, lambda q: q.timestamp.isoformat()) for t in tickers])
    check("bars", lambda: [fetched(data.bars(t, day.replace(day=1) if day.day > 1 else day, day), t, lambda b: len(b), lambda b: b[-1].day) for t in tickers[:3]])
    us = [t for t in tickers if t != "TSM"]
    check("fundamentals", lambda: [fetched(data.quarters(t), t, lambda q: q[-1].revenue, lambda q: q[-1].filed_date) for t in us[:3]])
    if "TSM" in tickers:  # a 20-F filer: annual IFRS only
        check("ifrs", lambda: [fetched(data.annuals("TSM"), "TSM", lambda a: a[-1].revenue, lambda a: a[-1].filed_date)])
    if "JPM" in tickers:  # a bank: us-gaap bank concepts
        check("bank", lambda: [fetched(data.quarters("JPM"), "JPM", lambda q: sorted(q[-1].extras) or None, lambda q: q[-1].filed_date)])
    check("earnings", lambda: [fetched(data.earnings(t), t, lambda e: e[-1].eps_actual, lambda e: e[-1].report_date) for t in tickers[:2]])
    check("news", lambda: [fetched(data.news(now.replace(hour=0), [t]), t, lambda n: len(n), lambda n: n[0].published_at.isoformat() if n else "") for t in tickers[:1]])

    def macro() -> list[dict[str, Any]]:
        snap, missing = data.macro_snapshot(now)
        if snap is None:
            raise ProviderError(missing or "거시 스냅샷 없음")
        return [{"ticker": sid, "value": s.latest.value, "provider": "fred", "timestamp": s.latest.source_ts.isoformat() if s.latest.source_ts else "", "source": s.latest.source}
                for sid, s in list(snap.series.items())[:5]]

    check("macro", macro)

    def estimates() -> list[dict[str, Any]]:
        # one request from the daily reserve (the scanner keeps a few of the ~25 free calls for manual use)
        out = data.prefetch_estimates(("NVDA",), day, budget=svc.base_cfg.scanner.estimate_daily_budget + 3, ttl_days=0)
        if out.get("NVDA") != "OK":
            raise ProviderError(f"alphavantage: {out.get('NVDA')}")
        av = next((p for p in data.reg.chain("analyst").providers if p.name == "alphavantage"), None)
        issues = getattr(av, "last_contract_issues", []) if av else []
        if issues:
            raise ProviderError("응답 필드 계약 불일치: " + "; ".join(issues[:3]))
        f = data.estimates("NVDA", day)
        return [fetched(f, "NVDA", lambda a: a.forward_eps, lambda a: a.as_of)]

    check("estimates", estimates)
    check("guidance", lambda: [{"ticker": "NVDA", "value": data.prefetch_guidance(("NVDA",), day).get("NVDA"), "provider": "sec-8k", "timestamp": day.isoformat(), "source": "sec.gov"}])
    check("short_interest", lambda: [fetched(data.short_interest(t), t, lambda o: o.short_interest_shares, lambda o: o.short_interest_settlement) for t in us[:2]])
    check("insider", lambda: [fetched(data.insider("NVDA"), "NVDA", lambda o: o.insider_net_buy_value_90d, lambda o: day)])

    report["summary"] = {k: v["status"] for k, v in report["categories"].items()}
    if record and svc.store is not None:
        svc.store.set_setting("live_verified", json.dumps({k: v["status"] == "VERIFIED" for k, v in report["categories"].items()}))
        svc.store.set_setting("live_verify_report", json.dumps({"at": datetime.now().isoformat(), "summary": report["summary"]}))
    return report
