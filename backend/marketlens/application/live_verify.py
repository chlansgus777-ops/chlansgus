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
import re
from datetime import date, datetime, timedelta
from typing import Any, Callable

from marketlens.domain.market_calendar import last_completed_session, to_ny
from marketlens.providers.contracts import ProviderError, ProviderUnavailable

TICKERS = ("NVDA", "AAPL", "MSFT", "JPM", "XOM", "AMZN", "TSM")
NETWORK_HINTS = ("http error", "ConnectError", "ProxyError", "timeout", "CONNECT")  # the host could not be reached


def _classify(err: Exception) -> str:
    """BLOCKED_BY_NETWORK when a configured provider could not be reached; BLOCKED_BY_CREDENTIAL when no provider
    of the category is configured (no key / no SEC User-Agent); FAILED for everything else (a real answer that was
    wrong, a contract mismatch, a circuit opened by earlier failures of the same provider in this run)."""
    msg = str(err)
    low = msg.lower()
    if any(h.lower() in low for h in NETWORK_HINTS):
        return "BLOCKED_BY_NETWORK"
    entries = re.findall(r"\('([^']+)', '([^']*)'\)", msg)  # AllProvidersFailed: [(provider, outcome), ...]
    if entries and all(o.startswith("not configured") for _, o in entries):
        return "BLOCKED_BY_CREDENTIAL"
    if "unauthorized (401)" in low or "unauthorized (403)" in low:
        return "BLOCKED_BY_CREDENTIAL"  # the provider answered and refused: key / licence / SEC User-Agent
    if "미설정" in msg or "not set" in low or "api_key" in low or "user_agent" in low or "blocked_by_credential" in low:
        return "BLOCKED_BY_CREDENTIAL"
    return "FAILED"


STORED_SOURCES = ("store", "cache", "fixture", "mock")
FAILURE_WORDS = ("실패", "없음", "error", "fail", "blocked", "403", "401", "429", "timeout", "unavailable", "미설정", "한도")


def sample_problem(s: dict[str, Any], now: datetime) -> str | None:
    """Why one sample does not prove the provider works (None = it does)."""
    for k in ("provider", "timestamp", "source"):
        if not s.get(k):
            return f"{k} 누락"
    if str(s.get("provider")).lower() in STORED_SOURCES or str(s.get("source")).lower() in STORED_SOURCES:
        return "로컬 저장소 값 — 공급자가 지금 응답했다는 증거가 아님"
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
    store_data = svc.data
    # every provider check calls the provider itself: no local store, no TTL cache in front of it. A value the
    # store already held proves nothing about whether the provider works today.
    from marketlens.application.data_access import DataAccess

    data = DataAccess(store_data.reg, {}, store=None, now_fn=svc.now)
    report: dict[str, Any] = {"as_of": now.isoformat(), "tickers": list(tickers), "categories": {}}

    def check(cat: str, fn: Callable[[], list[dict[str, Any]]]) -> None:
        # every category is judged by its own requests: a circuit opened by an earlier category's failure would
        # otherwise turn "not tried" into "failed"
        for ch in getattr(store_data.reg, "chains", {}).values():
            for br in getattr(ch, "breakers", {}).values():
                br.reset()
        try:
            samples = fn()
            problems = [f"{x.get('ticker')}: {p}" for x in samples if (p := sample_problem(x, now))] if samples else ["표본 없음"]
            ok = not problems
            report["categories"][cat] = {"status": "VERIFIED" if ok else "FAILED", "samples": samples[:7], "note": "; ".join(problems[:3])}
        except ProviderError as e:
            report["categories"][cat] = {"status": _classify(e), "samples": [], "note": str(e)[:600]}
        except Exception as e:  # noqa: BLE001 - a smoke test must report every failure, not crash
            report["categories"][cat] = {"status": "FAILED", "samples": [], "note": f"{type(e).__name__}: {str(e)[:180]}"}

    def fetched(f: Any, t: str, value: Callable[[Any], Any], ts: Callable[[Any], Any], positive: bool = False) -> dict[str, Any]:
        if f.error or f.value is None or (isinstance(f.value, (list, tuple)) and not f.value):
            raise ProviderError(f"{t}: {f.error or ('값 없음' if f.value is None else '값 없음(빈 목록)')}")
        return {"ticker": t, "value": value(f.value), "provider": f.provider, "timestamp": str(ts(f.value)), "source": f.provider, "positive": positive}

    check("price", lambda: [fetched(data.quote(t), t, lambda q: q.price, lambda q: q.timestamp.isoformat(), positive=True) for t in tickers])
    def bars() -> list[dict[str, Any]]:
        # the production path: ONE grouped-daily request for the last completed session (what the sync stores)
        grouped = next((p for p in store_data.reg.chain("price").providers if hasattr(p, "get_grouped_daily") and getattr(p, "configured", True)), None)
        if grouped is None:
            raise ProviderUnavailable("price: POLYGON_API_KEY 미설정 (일괄 일봉 공급자 없음)")
        got = grouped.get_grouped_daily(day)
        out = []
        for t in tickers[:3]:
            b = got.get(t)
            out.append({"ticker": t, "value": b.close if b else None, "provider": grouped.name, "timestamp": day.isoformat(), "source": f"{grouped.name}:grouped-daily", "positive": True})
        return out

    check("bars", bars)
    us = [t for t in tickers if t != "TSM"]
    check("fundamentals", lambda: [fetched(data.quarters(t), t, lambda q: q[-1].revenue, lambda q: q[-1].filed_date, positive=True) for t in us[:3]])

    def eps_ttm() -> list[dict[str, Any]]:
        # evaluation 3 R1: if real 10-Ks tag only annual diluted shares, Q4 EPS (and TTM EPS / P/E) would be
        # missing for most names — the fixtures cannot show this, only the real SEC data can
        from marketlens.domain.fundamentals import as_of as pit, compute_metrics

        out = []
        for t in [x for x in ("NVDA", "AAPL", "MSFT") if x in us] or us[:2]:
            f = data.quarters(t)
            if f.value is None:
                raise ProviderError(f"{t}: {f.error or '값 없음'}")
            m = compute_metrics(pit(f.value, obs_day))
            out.append({"ticker": t, "value": m.eps_ttm if m else None, "provider": f.provider, "timestamp": str(f.value[-1].filed_date), "source": f.provider})
        return out

    check("eps_ttm", eps_ttm)
    if "TSM" in tickers:  # a 20-F filer: annual IFRS only
        check("ifrs", lambda: [fetched(data.annuals("TSM"), "TSM", lambda a: a[-1].revenue, lambda a: a[-1].filed_date, positive=True)])
    if "JPM" in tickers:  # a bank: us-gaap bank concepts
        check("bank", lambda: [fetched(data.quarters("JPM"), "JPM", lambda q: sorted(q[-1].extras) or None, lambda q: q[-1].filed_date)])
    def earnings() -> list[dict[str, Any]]:
        out = []
        for t in tickers[:2]:
            f = data.earnings(t)
            if f.error or not f.value:
                # diagnostics: does a short recent window answer when the long one did not (a plan's history limit)?
                fh = next((p for p in data.reg.chain("analyst").providers if hasattr(p, "earnings_window") and getattr(p, "configured", True)), None)
                probe = ""
                if fh is not None:
                    try:
                        probe = f"; 최근 35일 창: {len(fh.earnings_window(t, 35))}건"
                    except ProviderError as e:
                        probe = f"; 최근 35일 창: {str(e)[:160]}"
                raise ProviderError(f"{t}: {f.error or '값 없음(빈 목록)'}{probe}")
            out.append(fetched(f, t, lambda e: e[-1].eps_actual, lambda e: e[-1].report_date))
        return out

    check("earnings", earnings)
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
        out = store_data.prefetch_estimates(("NVDA",), obs_day, budget=svc.base_cfg.scanner.estimate_daily_budget + 3, ttl_days=0)
        if out.get("NVDA") != "OK":
            raise ProviderError(f"alphavantage: {out.get('NVDA')}")
        av = next((p for p in data.reg.chain("analyst").providers if p.name == "alphavantage"), None)
        issues = getattr(av, "last_contract_issues", []) if av else []
        if issues:
            raise ProviderError("응답 필드 계약 불일치: " + "; ".join(issues[:3]))
        f = store_data.estimates("NVDA", obs_day)
        smp = fetched(f, "NVDA", lambda a: a.forward_eps, lambda a: a.as_of)
        smp["provider"] = smp["source"] = "alphavantage"  # the snapshot just requested (ttl 0), read back from the store
        return [smp]

    check("estimates", estimates)

    def guidance() -> list[dict[str, Any]]:
        from marketlens.domain.guidance import extract, html_to_text

        sec = next((p for p in store_data.reg.chain("fundamental").providers if hasattr(p, "earnings_releases") and getattr(p, "configured", True)), None)
        if sec is None:
            raise ProviderUnavailable("sec-8k: SEC 공급자 미설정")
        rel = sec.earnings_releases("NVDA", date.fromordinal(obs_day.toordinal() - 200))  # a real request, never "checked today"
        every = [i for r in rel for i in extract(html_to_text(r["text"]))]
        items = [i for i in every if i.status == "EXTRACTED"]
        if not rel:
            raise ProviderError("sec-8k: 최근 200일 실적 보도자료(8-K Item 2.02) 없음")
        if not items:
            # diagnostics: which documents, how long, what the extractor saw — so a real-data miss can be fixed
            from collections import Counter

            counts = Counter(i.status for i in every)
            seen = " | ".join(f"[{i.status} {i.metric}] {i.sentence[:140]}" for i in every[:3])
            docs = "; ".join(f"{r['url'].rsplit('/', 1)[-1]} ({len(r['text'])} chars)" for r in rel)
            raise ProviderError(f"sec-8k: 보도자료 {len(rel)}건에서 가이던스 수치를 하나도 추출하지 못함 — 문서: {docs}; 문장 상태: {dict(counts) or '후보 문장 없음'}; 예: {seen or '-'}")
        last = max(rel, key=lambda r: r["filed_at"])
        return [{"ticker": "NVDA", "value": len(items), "provider": "sec-8k", "timestamp": last["filed_at"].isoformat(), "source": last["url"], "positive": True}]

    check("guidance", guidance)
    check("short_interest", lambda: [fetched(data.short_interest(t), t, lambda o: o.short_interest_shares, lambda o: o.short_interest_settlement, positive=True) for t in us[:2]])
    check("insider", lambda: [fetched(data.insider("NVDA"), "NVDA", lambda o: o.insider_net_buy_value_90d, lambda o: day)])

    report["summary"] = {k: v["status"] for k, v in report["categories"].items()}
    if record and svc.store is not None:
        svc.store.set_setting("live_verified", json.dumps({k: v["status"] == "VERIFIED" for k, v in report["categories"].items()}))
        svc.store.set_setting("live_verify_report", json.dumps({"at": datetime.now().isoformat(), "summary": report["summary"]}))
    return report
