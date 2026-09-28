"""Why readiness stays below its gates — measured with the app's OWN providers against the real sources (Actions).

Owner report (2026-09-28): "60거래일 이상 가격 이력 86% < 90%, 시가총액 확인 57% < 80%, 대형주 분기 재무 76% < 80% (재무 태그
해석 불가 80종목)". For every ticker of the app's universe (SEC company_tickers_exchange, Nasdaq/NYSE/NYSE American):
- does the Polygon grouped download contain it (as written by SEC, and with the class separator '-' → '.')?
- does the SEC shares frame have its CIK, and would the sync hand the shares to THIS ticker (one ticker per CIK)?
- for large caps (frame shares × close ≥ $1B): does the app's SEC parser read its quarters, and if not, why?
Prints one JSON document; no secret is ever printed. Rates: Polygon 1 call, SEC ≤ 4 requests/second.

    python scripts/diagnose_readiness.py --out diagnose.json
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from marketlens.domain.market_calendar import last_completed_session  # noqa: E402
from marketlens.providers.contracts import ProviderError  # noqa: E402
from marketlens.providers.live.polygon import PolygonProvider  # noqa: E402
from marketlens.providers.live.sec_edgar import SecEdgarProvider  # noqa: E402


def kind(ticker: str, name: str) -> str:
    n = name.upper()
    if re.search(r"\bWARRANTS?\b", n) or re.search(r"(-WT|\.WS|-WS|WS)$", ticker):
        return "warrant"
    if re.search(r"\bRIGHTS?\b", n):
        return "right"
    if re.search(r"\bUNITS?\b", n) or (ticker.endswith("U") and "ACQUISITION" in n):
        return "unit"
    if re.search(r"-P[A-Z]?$", ticker) or "PREFERRED" in n or "DEPOSITARY SH" in n:
        return "preferred"
    if re.search(r"\b(ETF|FUND|TRUST|PORTFOLIO|SHARES|INDEX)\b", n):
        return "fund_or_trust"
    if re.search(r"-[A-Z]$", ticker):
        return "share_class"
    return "common"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="diagnose.json")
    ap.add_argument("--max-large", type=int, default=3000)
    a = ap.parse_args()
    sec = SecEdgarProvider(os.environ.get("SEC_USER_AGENT"), rate_per_s=4.0)
    poly = PolygonProvider(os.environ.get("POLYGON_API_KEY"))
    now = datetime.now(timezone.utc)
    day = last_completed_session(now)
    secs = sec.list_securities(None)
    grouped = poly.get_grouped_daily(day)
    frames = sec.shares_outstanding_all(day)
    first_of_cik: dict[int, str] = {}
    for t, c in sec._cik.items():  # the sync's ticker_by_cik: the FIRST ticker of each CIK gets the shares
        first_of_cik.setdefault(c, t)
    rows = []
    for s in secs:
        t = s.ticker
        dot = re.sub(r"-(?=[A-Z]{1,2}$)", ".", t)
        k = kind(t, s.company_name)
        bar = grouped.get(t) or grouped.get(dot)
        shares = frames.get(s.cik or -1)
        rows.append({
            "ticker": t, "name": s.company_name, "cik": s.cik, "kind": k,
            "bars_exact": t in grouped, "bars_dot": dot != t and dot in grouped,
            "frame_shares": shares is not None, "gets_shares_now": shares is not None and first_of_cik.get(s.cik) == t,
            "cap_after_fix": (bar.close * shares[0]) if (bar is not None and shares is not None) else None,
            "cap_now": (grouped[t].close * shares[0]) if (t in grouped and shares is not None and first_of_cik.get(s.cik) == t) else None,
        })
    n = len(rows)
    by_kind = Counter(r["kind"] for r in rows)
    out: dict = {"session": day.isoformat(), "listed": n, "by_kind": dict(by_kind), "grouped_rows": len(grouped), "frame_ciks": len(frames)}
    out["bars_now"] = sum(r["bars_exact"] for r in rows) / n
    out["bars_after_dot_fix"] = sum(r["bars_exact"] or r["bars_dot"] for r in rows) / n
    out["missing_bars_by_kind_after_fix"] = dict(Counter(r["kind"] for r in rows if not (r["bars_exact"] or r["bars_dot"])))
    out["cap_now"] = sum(r["cap_now"] is not None for r in rows) / n
    out["cap_after_fixes"] = sum(r["cap_after_fix"] is not None for r in rows) / n
    out["cap_missing_after_fixes_by_kind"] = dict(Counter(r["kind"] for r in rows if r["cap_after_fix"] is None))
    out["cap_missing_reason_after_fixes"] = dict(Counter(
        ("no_bars" if not (r["bars_exact"] or r["bars_dot"]) else "") + ("no_frame_shares" if not r["frame_shares"] else "")
        for r in rows if r["cap_after_fix"] is None))
    out["dot_fix_examples"] = [r["ticker"] for r in rows if r["bars_dot"]][:40]
    out["shares_to_wrong_ticker_examples"] = [r["ticker"] for r in rows if r["frame_shares"] and not r["gets_shares_now"] and r["kind"] in ("common", "share_class")][:40]
    # ---------------------------------------------------------------- large caps: the SEC parser
    large = sorted((r for r in rows if (r["cap_after_fix"] or 0) >= 1e9), key=lambda r: -r["cap_after_fix"])[: a.max_large]
    fails = []
    ok = 0
    for r in large:
        try:
            qs = sec.get_quarterly(r["ticker"])
            ok += 1 if qs else 0
        except ProviderError as e:
            prof = {}
            try:
                prof = sec.company_profile(r["ticker"])
            except ProviderError:
                pass
            fails.append({"ticker": r["ticker"], "name": r["name"], "cap": round(r["cap_after_fix"] / 1e9, 1), "kind": r["kind"],
                          "error": f"{type(e).__name__}: {str(e)[:160]}", "foreign": prof.get("foreign_issuer"), "country": prof.get("country"),
                          "forms": prof.get("recent_forms") if isinstance(prof.get("recent_forms"), list) else None})
    out["large"] = len(large)
    out["large_parsed"] = ok
    out["large_failed"] = len(fails)
    out["large_failed_by_error"] = dict(Counter(f["error"].split(":")[0] + ":" + f["error"].split(":", 2)[-1][:60] for f in fails).most_common(15))
    out["large_failed_foreign"] = sum(1 for f in fails if f["foreign"])
    out["large_failed_list"] = fails
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1, ensure_ascii=False)
    summary = {k: v for k, v in out.items() if k != "large_failed_list"}
    print(json.dumps(summary, indent=1, ensure_ascii=False))
    print("LARGE_FAILED (ticker | cap $B | foreign | error):")
    for f in fails:
        print(f"  {f['ticker']:8} {f['cap']:>8} {str(f['foreign']):5} {f['name'][:40]:40} {f['error'][:110]}")


if __name__ == "__main__":
    main()
