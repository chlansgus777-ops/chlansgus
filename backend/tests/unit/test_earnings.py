from datetime import date

from marketlens.domain.earnings import (
    AnalystSnapshot, EarningsReport, ExpectationBar, Guidance, ResultQuality, assess_earnings, assess_revisions,
)


def rep(rev_a=110, rev_c=100, eps_a=1.1, eps_c=1.0, g_lo=None, g_hi=None, g_c=None, run=0.0, d=date(2026, 8, 1)):
    return EarningsReport(d, "Q", "t", revenue_actual=rev_a, revenue_consensus=rev_c, eps_actual=eps_a, eps_consensus=eps_c,
                          guidance=Guidance(next_q_revenue_low=g_lo, next_q_revenue_high=g_hi, next_q_revenue_consensus=g_c), pre_earnings_run_pct=run)


def test_beat_and_raise():
    a = assess_earnings([rep(g_lo=105, g_hi=115, g_c=100)])
    assert a.result_quality == ResultQuality.BEAT_AND_RAISE
    assert a.revenue_surprise > 0 and a.guide_rev_vs_cons > 0


def test_good_number_but_below_expectations_is_a_miss():
    # revenue grew (absolute good) but came in below consensus
    a = assess_earnings([rep(rev_a=150, rev_c=160, eps_a=2.0, eps_c=2.2)])
    assert a.result_quality == ResultQuality.MISS


def test_beat_with_weak_guide():
    a = assess_earnings([rep(g_lo=90, g_hi=94, g_c=100)])
    assert a.result_quality == ResultQuality.BEAT_WEAK_GUIDE
    assert any("질 낮은 서프라이즈" in n for n in a.notes)


def test_expectation_bar_high_after_run_up():
    assert assess_earnings([rep(run=0.25)]).expectation_bar == ExpectationBar.HIGH
    assert assess_earnings([rep(run=-0.2)]).expectation_bar == ExpectationBar.LOW


def test_no_data_unknown():
    assert assess_earnings([]) is None
    a = assess_earnings([EarningsReport(date(2026, 1, 1), "Q", "t")])
    assert a.result_quality == ResultQuality.UNKNOWN


def test_revision_direction_and_coverage():
    a = AnalystSnapshot(date(2026, 9, 1), "t", eps_revision_7d=0.01, eps_revision_30d=0.03, eps_revision_90d=0.06,
                        revenue_revision_30d=-0.02, revenue_revision_90d=-0.01, analyst_count=3, estimate_dispersion=0.4)
    r = assess_revisions(a)
    assert r.eps_direction == 1 and r.revenue_direction == -1
    assert r.low_coverage and r.high_dispersion
    assert 0.5 < r.breadth_score <= 1.0
    assert assess_revisions(None) is None
