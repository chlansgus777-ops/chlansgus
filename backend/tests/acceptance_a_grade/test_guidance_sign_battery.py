"""Guidance sign battery (after evaluations 2-5). The rule: a wrong sign stored as EXTRACTED is never acceptable;
GUIDANCE_UNCLEAR (no value) is acceptable wherever the sign or the measure cannot be read deterministically.

Each case: (sentence, expectation) — "+" / "-" must be EXTRACTED with that sign, "+?" / "-?" may be that sign or
UNCLEAR, "?" must be UNCLEAR (a range crossing zero, two measures, a total next to a per-share figure …)."""

from __future__ import annotations

import pytest

from marketlens.domain.guidance import extract

CASES = [
    # clear positives
    ("For fiscal 2027, we expect diluted EPS of $1.10 to $1.20.", "+"),
    ("For fiscal 2027, we expect EPS of $0.40–$0.45.", "+"),
    ("For fiscal 2027, we expect EPS of $0.40 - $0.45.", "+"),
    ("For fiscal 2027, we expect operating margin of 12% to 13%.", "+"),
    ("For fiscal 2027, we expect revenue of $3.2 billion to $3.4 billion.", "+"),
    ("For fiscal 2027, we expect revenue of $3.2 billion – $3.4 billion.", "+"),
    ("Revenue is expected to be $54.0 billion, plus or minus 2%.", "+"),
    ("For fiscal 2027, the Company expects to be profitable, with EPS of $0.05 to $0.10.", "+?"),
    # clear losses
    ("Net loss per share for fiscal 2027 is expected to be $1.10 to $1.20.", "-"),
    ("We now expect a smaller net loss per share of $0.10 to $0.12 for fiscal 2027.", "-"),
    ("For fiscal 2027, net loss per diluted share is expected to improve to $0.10.", "-"),
    ("For fiscal 2027, we expect loss per share of approximately $0.25.", "-"),
    ("For fiscal 2027, we expect net loss per share attributable to common stockholders of $0.40 to $0.45.", "-"),
    ("For fiscal 2027, we expect non-GAAP net loss per share of $0.40 to $0.45.", "-?"),
    # losses without the clear form, explicit minus signs → negative or UNCLEAR, never positive
    ("The Company expects losses per share of $0.30 to $0.35 for fiscal 2027.", "-?"),
    ("For fiscal 2027, the Company expects to lose $0.30 per share.", "-?"),
    ("For fiscal 2027, we expect EPS of −$0.10 to −$0.05.", "-?"),
    ("For fiscal 2027, we expect EPS of ($0.10) to ($0.05).", "-?"),
    ("For fiscal 2027, we expect EPS of approximately ‒$0.10.", "-?"),
    ("For fiscal 2027, we expect operating margin of –3% to –1%.", "-?"),
    ("For fiscal 2027, we expect net income (loss) per share of $(0.10) to $(0.12).", "-?"),
    ("Net loss per share, excluding charges, is expected to be $0.10 for fiscal 2027.", "-?"),
    # profits next to an unrelated loss → positive or UNCLEAR, never negative
    ("We expect net loss per share to turn into positive EPS of $0.05 in fiscal 2027.", "+?"),
    ("For fiscal 2027, we expect diluted EPS of $0.10 to $0.12 despite losses in our European unit.", "+?"),
    ("For fiscal 2027, we expect gross margin of 45% to 46%, excluding inventory losses.", "+?"),
    ("For fiscal 2027, we expect EPS of $1.10 to $1.20, including a $0.05 per share charge.", "+?"),
    ("For fiscal 2027, we expect EPS of $0.10 to $0.12, down from $0.20.", "+?"),
    ("For fiscal 2027, we expect breakeven to $0.05 EPS.", "+?"),
    # undecidable → UNCLEAR
    ("For fiscal 2027, we expect diluted EPS in the range of negative $0.05 to positive $0.02.", "?"),
    ("For fiscal 2027, EPS is expected to be between a loss of $0.05 and a profit of $0.02.", "?"),
    ("For fiscal 2027, we expect GAAP EPS of $1.00 to $1.10 and non-GAAP EPS of $1.30 to $1.40.", "?"),
    ("For fiscal 2027, we expect GAAP net loss per share of $0.40 to $0.45 and adjusted EPS of $0.10 to $0.12.", "?"),
    ("We expect adjusted EPS of $0.40 to $0.50, while GAAP net loss per share is expected to be $0.10 to $0.20.", "?"),
    ("For fiscal 2027, we expect net loss of $40 million to $45 million, or $0.40 to $0.45 per share.", "?"),
    ("For fiscal 2027, we expect net income of $40 million to $45 million, or $0.40 to $0.45 per share.", "?"),
    ("GAAP and non-GAAP gross margins are expected to be 73.3% and 73.5%, respectively, plus or minus 50 basis points.", "?"),
]


@pytest.mark.parametrize("sentence, expect", CASES)
def test_guidance_sign_is_never_wrong(sentence, expect):
    (item,) = extract(sentence)
    if item.status != "EXTRACTED":
        assert expect in ("+?", "-?", "?"), f"expected a {expect} value, got UNCLEAR"
        return
    assert expect != "?", f"must be UNCLEAR, got {item.low}..{item.high}"
    sign = "-" if item.high < 0 else "+"
    assert item.low <= item.high and (item.low < 0) == (item.high < 0), (item.low, item.high)
    assert sign == expect[0], f"WRONG SIGN: expected {expect}, stored {item.low}..{item.high}"
