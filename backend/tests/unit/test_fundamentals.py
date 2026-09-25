from datetime import date

import pytest

from marketlens.domain.fundamentals import QuarterlyFinancials, as_of, compute_metrics


def q(i: int, rev: float, eps: float = 1.0, filed_offset: int = 35, **kw) -> QuarterlyFinancials:
    y, m = 2024 + (i // 4), (i % 4) * 3 + 3
    pe = date(y, m, 30 if m in (6, 9) else 31)
    from datetime import timedelta

    return QuarterlyFinancials(period_end=pe, filed_date=pe + timedelta(days=filed_offset), fiscal_label=str(i), source="t",
                               revenue=rev, gross_profit=rev * 0.5, operating_income=rev * 0.2, net_income=rev * 0.1,
                               eps_diluted=eps, operating_cash_flow=rev * 0.2, capex=rev * 0.05, depreciation_amortization=rev * 0.02,
                               cash=100, total_debt=300, total_equity=500, shares_diluted=100, **kw)


def test_ttm_yoy_qoq():
    qs = [q(i, 100 + 10 * i, eps=1 + 0.1 * i) for i in range(8)]
    m = compute_metrics(qs)
    assert m.revenue_ttm == sum(100 + 10 * i for i in range(4, 8))
    assert m.revenue_growth_yoy == pytest.approx(170 / 130 - 1)
    assert m.revenue_growth_qoq == pytest.approx(170 / 160 - 1)
    assert m.fcf_ttm == pytest.approx(m.revenue_ttm * 0.15)
    assert m.net_debt == 200
    assert m.ebitda_ttm == pytest.approx(m.revenue_ttm * 0.22)
    assert m.revenue_trend_4q is not None and m.revenue_trend_4q > 0


def test_growth_from_negative_base_is_not_meaningful():
    qs = [q(i, 100, eps=-1.0 if i < 4 else 1.0) for i in range(8)]
    assert compute_metrics(qs).eps_growth_yoy is None


def test_point_in_time_excludes_unfiled_quarters():
    qs = [q(i, 100 + i) for i in range(8)]
    last = qs[-1]
    before_filing = as_of(qs, last.filed_date.replace(day=1) if last.filed_date.day > 1 else last.filed_date)
    assert last not in before_filing
    assert as_of(qs, last.filed_date)[-1] == last


def test_insufficient_quarters_gives_none():
    m = compute_metrics([q(0, 100)])
    assert m.revenue_ttm is None and m.revenue_growth_yoy is None
