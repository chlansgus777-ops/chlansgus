"""Bank KPIs from SEC XBRL (us-gaap) quarterly data — the part of a bank model free data can support.

Computed: loan growth, deposit growth, provision/loans, net charge-off rate, ROTCE, ROE, tangible book
value, and CET1 only when the bank tags it in XBRL. NIM needs average earning assets (not a standard tag)
and is therefore not computed; FFIEC call-report data would be required (not connected).
"""

from __future__ import annotations

from typing import Sequence

from marketlens.domain.fundamentals import QuarterlyFinancials, consecutive


def _x(q: QuarterlyFinancials, k: str) -> float | None:
    v = q.extras.get(k) if q.extras else None
    return float(v) if v is not None else None


def bank_features(qs: Sequence[QuarterlyFinancials]) -> dict[str, float | None]:
    if not qs or not any(q.extras for q in qs):
        return {}
    qs = sorted(qs, key=lambda q: q.period_end)
    cur = qs[-1]
    out: dict[str, float | None] = {}
    yoy = qs[-5] if len(qs) >= 5 and consecutive(qs[-5:]) else None
    for k, name in (("loans", "loan_growth"), ("deposits", "deposit_growth")):
        a, b = _x(cur, k), _x(yoy, k) if yoy else None
        out[name] = (a / b - 1) if a is not None and b else None
    loans = _x(cur, "loans")
    last4 = qs[-4:] if len(qs) >= 4 and consecutive(qs[-4:]) else []
    prov = [_x(q, "provision") for q in last4]
    out["provision_to_loans"] = sum(prov) / loans if last4 and all(p is not None for p in prov) and loans else None  # type: ignore[arg-type]
    nco = [_x(q, "net_charge_offs") for q in last4]
    out["charge_off_rate"] = sum(nco) / loans if last4 and all(n is not None for n in nco) and loans else None  # type: ignore[arg-type]
    ni = [q.net_income for q in last4]
    ni_ttm = sum(ni) if last4 and all(n is not None for n in ni) else None  # type: ignore[arg-type]
    eq, gw, ia = cur.total_equity, _x(cur, "goodwill"), _x(cur, "intangibles")
    tbv = eq - (gw or 0.0) - (ia or 0.0) if eq is not None and gw is not None else None  # goodwill must be known
    out["tangible_book_value"] = tbv
    out["rotce"] = ni_ttm / tbv if ni_ttm is not None and tbv and tbv > 0 else None
    out["roe"] = ni_ttm / eq if ni_ttm is not None and eq and eq > 0 else None
    out["cet1"] = _x(cur, "cet1_ratio")
    return out
