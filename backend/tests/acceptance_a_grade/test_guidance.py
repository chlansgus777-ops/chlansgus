"""A-grade acceptance: SEC earnings-release guidance is extracted deterministically, never invented."""

from __future__ import annotations

import pytest

from marketlens.domain.guidance import extract, html_to_text

RELEASE = """<html><body><p>Outlook</p>
<p>Outlook for the third quarter of fiscal 2027 is as follows:</p>
<ul><li>Revenue is expected to be $54.0 billion, plus or minus 2%.</li>
<li>GAAP gross margins are expected to be 73.5%, plus or minus 50 basis points.</li>
<li>Capital expenditures are expected to be between $1.1 billion and $1.3 billion for fiscal 2027.</li>
<li>The company expects strong demand to continue.</li></ul>
<p>Revenue for the second quarter was $46.7 billion, up 56% from a year ago.</p></body></html>"""


def by(items, metric):
    return next(i for i in items if i.metric == metric)


def test_recognised_forms_are_extracted_with_their_source_sentence():
    items = extract(html_to_text(RELEASE))
    rev = by(items, "revenue")
    assert rev.status == "EXTRACTED" and rev.low == pytest.approx(52.92e9) and rev.high == pytest.approx(55.08e9) and rev.unit == "USD"
    assert "plus or minus 2%" in rev.sentence
    gm = by(items, "gross_margin")
    assert gm.low == pytest.approx(0.730) and gm.high == pytest.approx(0.740) and gm.unit == "fraction"
    cap = by(items, "capex")
    assert (cap.low, cap.high) == (pytest.approx(1.1e9), pytest.approx(1.3e9)) and cap.period_label and "2027" in cap.period_label
    assert all(i.metric != "revenue" or "expected" in i.sentence for i in items)  # the reported $46.7B is not guidance


def test_every_extracted_number_is_written_in_the_sentence():
    for i in extract(html_to_text(RELEASE)):
        if i.status == "EXTRACTED" and i.unit == "USD":
            head = f"{i.low / 1e9:.1f}" if i.low == i.high else None
            if head:
                assert head in i.sentence


def test_unclear_and_withdrawn_guidance_are_not_turned_into_numbers():
    text = "We expect revenue to grow meaningfully next year. Due to uncertainty the company will not provide quarterly guidance."
    items = extract(text)
    assert [i.status for i in items] == ["GUIDANCE_UNCLEAR", "NO_GUIDANCE"]
    assert all(i.low is None and i.high is None for i in items)
    mixed = extract("We expect revenue of $10 billion and EPS of $2.00 for fiscal 2027.")
    assert mixed[0].status == "GUIDANCE_UNCLEAR"  # two metrics in one sentence → not guessed apart


def test_guidance_is_compared_only_with_the_consensus_known_before_the_release():
    from datetime import date, datetime, timezone

    from marketlens.application.estimate_book import attach_guidance
    from marketlens.domain.earnings import EarningsReport
    from marketlens.domain.estimates import EstimateObservation

    class Row:  # shape of a stored GuidanceRow
        def __init__(self, metric, low, high, label):
            self.metric, self.low, self.high, self.period_label = metric, low, high, label
            self.status, self.confidence, self.accession = "EXTRACTED", "MEDIUM", "A1"
            self.filed_at = datetime(2026, 7, 28, 20, 5, tzinfo=timezone.utc)
            self.sentence, self.source_url = f"{metric} is expected to be …", "https://www.sec.gov/x/ex991.htm"

    rows = [Row("revenue", 32.34e9, 33.66e9, "third quarter of fiscal 2026")]
    snap = lambda day, rev: EstimateObservation("NVDA", "finnhub", "FQ2026Q3", "quarter", None, day, eps=0.5, revenue=rev, report_date=date(2026, 10, 28))  # noqa: E731
    hist = [snap(date(2026, 7, 20), 31.0e9), snap(date(2026, 7, 27), 31.5e9), snap(date(2026, 7, 29), 33.2e9)]  # the last one is post-release
    reports = [EarningsReport(date(2026, 7, 28), "Q2 2026", "finnhub", revenue_actual=30.2e9)]
    g = attach_guidance(reports, rows, hist, date(2026, 8, 5))[0].guidance
    assert g.next_q_revenue_consensus == 31.5e9  # the snapshot of the day before, never the revised post-release one
    assert g.next_q_revenue_low == 32.34e9 and g.evidence and g.source
    before = attach_guidance(reports, rows, hist, date(2026, 7, 27))[0].guidance  # not filed yet on that day
    assert before.next_q_revenue_low is None
