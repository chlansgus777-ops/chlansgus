"""Why a run bought so rarely: the actions, the gates and the scores of its stored rows.

    python -m marketlens.backtest.actions bt-out/rows.db

Counts the eligible rows by action and by the action before the gates (raw_action), the vetoes that stopped a
bullish raw action, the score percentiles, how many rows reached the BUY SMALL / BUY thresholds, and the
completeness of the data behind them — per year as well, since the universe grows over the window."""

from __future__ import annotations

import json
import sqlite3
import sys
from collections import Counter, defaultdict
from typing import Any

from marketlens.domain.enums import BULLISH_ACTIONS

BULLISH = {a.value for a in BULLISH_ACTIONS}


def pct(xs: list[float], q: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return round(s[min(len(s) - 1, int(q * len(s)))], 2)


def summarize(path: str, buy_small: float = 72.0, buy: float = 80.0) -> dict[str, Any]:
    con = sqlite3.connect(path)
    action, raw, vetoes, gated_by = Counter(), Counter(), Counter(), Counter()
    totals: list[float] = []
    comp: list[float] = []
    per_year: dict[str, Counter] = defaultdict(Counter)
    comps: dict[str, list[float]] = defaultdict(list)
    avail: Counter = Counter()
    n = 0
    for t, payload in con.execute("SELECT t, payload FROM bt_rows WHERE eligible = 1"):
        p = json.loads(payload)
        n += 1
        y = t[:4]
        action[p["action"]] += 1
        raw[p["raw_action"]] += 1
        for v in p.get("vetoes") or []:
            vetoes[v] += 1
        if p["raw_action"] in BULLISH and p["action"] not in BULLISH:
            gated_by["+".join(sorted(p.get("vetoes") or [])) or "(no veto recorded)"] += 1
        totals.append(p["total"])
        comp.append(p.get("completeness") or 0.0)
        per_year[y]["rows"] += 1
        per_year[y]["bullish"] += p["action"] in BULLISH
        per_year[y]["raw_bullish"] += p["raw_action"] in BULLISH
        per_year[y][">=72"] += p["total"] >= buy_small
        for name, c in (p.get("components") or {}).items():
            avail[name] += bool(c.get("available"))
            if c.get("available") and c.get("sub") is not None:
                comps[name].append(c["sub"])
    return {
        "eligible_rows": n,
        "action": dict(action.most_common()), "raw_action": dict(raw.most_common()),
        "vetoes": dict(vetoes.most_common(15)), "bullish_raw_gated_by": dict(gated_by.most_common(15)),
        "total_percentiles": {q: pct(totals, q) for q in (0.5, 0.9, 0.99, 0.999)},
        "rows_at_or_above": {str(buy_small): sum(x >= buy_small for x in totals), str(buy): sum(x >= buy for x in totals)},
        "completeness_percentiles": {q: pct(comp, q) for q in (0.1, 0.5, 0.9)},
        "component_available_share": {k: round(v / n, 3) for k, v in sorted(avail.items())} if n else {},
        "component_sub_median": {k: pct(v, 0.5) for k, v in sorted(comps.items())},
        "per_year": {y: dict(c) for y, c in sorted(per_year.items())},
    }


def main(argv: list[str] | None = None) -> int:
    print(json.dumps(summarize((argv or sys.argv[1:])[0]), ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
