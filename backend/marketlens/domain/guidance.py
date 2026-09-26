"""Deterministic extraction of management guidance from an earnings-release text (SEC 8-K, Exhibit 99).

Rules (no LLM, no inference):
- A candidate sentence must contain a forward-looking word ("expect", "outlook", "guidance", …) and
  exactly one metric (revenue, EPS, gross margin, operating margin, capex).
- Only numbers written in that sentence are stored, with the sentence itself as evidence.
- Recognised forms: "$54.0 billion, plus or minus 2%", "between $1.10 and $1.20", "$3.2 to $3.4 billion",
  "73.5%, plus or minus 50 basis points", "in the range of 45% to 46%", a single "$X billion".
- Anything else about a metric in a forward-looking sentence → GUIDANCE_UNCLEAR (kept, value None).
- "does not provide guidance", "withdraw(s/n) … guidance" → NO_GUIDANCE.
- Sign: only wording attached to the guided number decides it. "loss per diluted share of $1.10 to $1.20"
  and "net loss of $0.30 to $0.40" are stored negative (low = -1.20, high = -1.10). A loss mentioned
  elsewhere in the sentence ("excluding the loss on the sale …", "compared with a net loss last year",
  "credit losses") leaves the guided number positive. An explicit negative number ("-$0.10", "$(0.10)"),
  the word "negative", a range from a loss to a profit, or a loss attached to revenue / gross margin /
  capex → GUIDANCE_UNCLEAR, because the sign cannot be read deterministically.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

FORWARD = ("expect", "outlook", "guidance", "anticipate", "forecast", "project", "we see ", "will be in the range", "is expected")
NO_GUIDE = re.compile(r"(does not|do not|will not|won't)\s+provide\s+(\w+\s+)?(guidance|outlook)|withdr[ae]w\w*\s+(its\s+|our\s+|the\s+)?(\w+\s+)?(guidance|outlook)", re.I)
METRICS: list[tuple[str, tuple[str, ...]]] = [
    ("eps", ("earnings per share", "loss per share", "per diluted share", "per share", "diluted eps", "eps")),
    ("gross_margin", ("gross margin",)),
    ("operating_margin", ("operating margin",)),
    ("capex", ("capital expenditures", "capex")),
    ("revenue", ("revenue", "revenues", "net sales", "total sales")),
]
_NUM = r"(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?"
_SCALE = {"billion": 1e9, "million": 1e6, "thousand": 1e3, "b": 1e9, "m": 1e6}
PERIOD_RE = re.compile(r"((first|second|third|fourth)\s+(fiscal\s+)?quarter(\s+of)?(\s+fiscal)?(\s+(year\s+)?20\d\d)?|(full[- ]year|fiscal(\s+year)?)\s+20\d\d|fiscal\s+20\d\d|full[- ]year|q[1-4]\s*(fy)?\s*20\d\d)", re.I)


@dataclass(frozen=True, slots=True)
class GuidanceItem:
    metric: str
    low: float | None
    high: float | None
    unit: str  # USD | USD/share | fraction
    period_label: str | None
    sentence: str
    status: str  # EXTRACTED | GUIDANCE_UNCLEAR | NO_GUIDANCE
    confidence: str  # MEDIUM | LOW

    @property
    def mid(self) -> float | None:
        return (self.low + self.high) / 2 if self.low is not None and self.high is not None else None


def html_to_text(doc: str) -> str:
    doc = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", doc)
    doc = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>", "\n", doc)
    doc = re.sub(r"<[^>]+>", " ", doc)
    return re.sub(r"[ \t\xa0]+", " ", html.unescape(doc))


def sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;])\s+(?=[A-Z•\-–])|\n+|•", text)
    return [p.strip(" -–•\t") for p in parts if len(p.strip()) > 12]


def _val(num: str, frac: str | None) -> float:
    return float(num.replace(",", "") + ("." + frac if frac else ""))


def _metric(s: str) -> list[str]:
    low = s.lower()
    found = []
    for m, words in METRICS:
        if any(re.search(r"(?<![a-z])" + re.escape(w) + r"s?(?![a-z])", low) for w in words):
            found.append(m)
    # "gross margin"/"operating margin" also contain no "revenue"; "revenue" inside "revenue growth" is fine
    return found


def _parse(metric: str, s: str) -> tuple[float | None, float | None, str, bool, int]:
    """(low, high, unit, clean, start). ``clean`` = matched one of the recognised forms exactly; ``start`` =
    where the guided number expression begins in the sentence (-1 = nothing parsed)."""
    low = s.lower()
    if metric in ("gross_margin", "operating_margin"):
        m = re.search(_NUM + r"\s*%\s*,?\s*plus or minus\s*" + _NUM + r"\s*basis points", low)
        if m:
            c, bp = _val(m.group(1), m.group(2)), _val(m.group(3), m.group(4))
            return (c - bp / 100) / 100, (c + bp / 100) / 100, "fraction", True, m.start()
        m = re.search(r"(?:between|range of|from)?\s*" + _NUM + r"\s*%?\s*(?:to|and|-|–)\s*" + _NUM + r"\s*%", low)
        if m:
            return _val(m.group(1), m.group(2)) / 100, _val(m.group(3), m.group(4)) / 100, "fraction", True, m.start(1)
        ms = list(re.finditer(_NUM + r"\s*%", low))
        if len(ms) == 1:
            v = _val(ms[0].group(1), ms[0].group(2)) / 100
            return v, v, "fraction", False, ms[0].start()
        return None, None, "fraction", False, -1
    unit = "USD/share" if metric == "eps" else "USD"
    scale_word = r"\s*(billion|million|thousand|b|m)?(?![a-z])"
    m = re.search(r"\$\s?" + _NUM + scale_word + r"\s*,?\s*plus or minus\s*" + _NUM + r"\s*%", low)
    if m:
        c = _val(m.group(1), m.group(2)) * (_SCALE.get(m.group(3) or "", 1.0) if metric != "eps" else 1.0)
        p = _val(m.group(4), m.group(5)) / 100
        return c * (1 - p), c * (1 + p), unit, True, m.start()
    m = re.search(r"\$\s?" + _NUM + scale_word + r"\s*(?:to|and|-|–)\s*\$?\s?" + _NUM + scale_word, low)
    if m:
        sc = _SCALE.get(m.group(6) or m.group(3) or "", 1.0) if metric != "eps" else 1.0
        return _val(m.group(1), m.group(2)) * sc, _val(m.group(4), m.group(5)) * sc, unit, True, m.start()
    ms = list(re.finditer(r"\$\s?" + _NUM + scale_word, low))
    if len(ms) == 1:
        sc = _SCALE.get(ms[0].group(3) or "", 1.0) if metric != "eps" else 1.0
        v = _val(ms[0].group(1), ms[0].group(2)) * sc
        return v, v, unit, metric != "eps" and sc > 1, ms[0].start()
    return None, None, unit, False, -1


# A loss phrase that ends right where the guided number starts ("loss per diluted share of $1.10 to …",
# "net loss of $0.30 to $0.40", "a loss in the range of $0.10 to $0.15"). Only then is the number a loss.
_LOSS_ATTACHED = re.compile(
    r"(?<![a-z])(loss|losses)(\s+per\s+(diluted\s+|basic\s+)?(common\s+)?share)?"
    r"(\s+(is\s+|are\s+)?(expected\s+|projected\s+)?(to\s+be\s+|will\s+be\s+)?)?"
    r"(\s*,\s*|\s+)?(of\s+|between\s+|in\s+the\s+range\s+of\s+|ranging\s+from\s+|from\s+|approximately\s+|about\s+|of\s+approximately\s+)*$")
_PROFIT = re.compile(r"(?<![a-z])(profit|profitable|profitability|net income|income|earnings|break-?\s?even|positive)(?![a-z])")
_NEG_MARK = re.compile(r"(?:^|(?<=[\s(]))[-−](?=\s?\$?\s?\d)|\$\s?\(\s?\d|\(\s?\$\s?\d|(?<!or )minus\s+\$?\s?\d")
_RANGE_LEFT = re.compile(r"(\d|%|billion|million|thousand|\bb|\bm)\s*$")


def _has_negative_number(low: str) -> bool:
    """An explicitly negative number ("-$0.10", "$(0.10)"); a dash between two amounts
    ("$3.2 billion - $3.4 billion", "45% - 46%") is a range, not a sign."""
    for m in _NEG_MARK.finditer(low):
        if m.group(0) in ("-", "−") and _RANGE_LEFT.search(low[: m.start()]):
            continue
        return True
    return False


def _sign(metric: str, s: str, start: int) -> str:
    """POS | NEG | UNCLEAR for the guided number that starts at ``start``.

    A loss word elsewhere in the sentence ("excluding the loss on the sale …", "compared with a net loss of
    $0.50 last year", "credit losses") says nothing about the guided number, which stays positive. Only a
    loss phrase attached to the guided number makes it negative. An explicit negative sign, the word
    "negative", or a range that runs from a loss to a profit cannot be read deterministically → UNCLEAR."""
    low = s.lower()
    if _has_negative_number(low) or re.search(r"(?<![a-z])negative(?![a-z])", low):
        return "UNCLEAR"
    if metric not in ("eps", "operating_margin"):
        # revenue / gross margin / capex cannot be a loss: a loss word in the same sentence means something
        # else is being discussed next to the number — kept as UNCLEAR rather than guessed (conservative)
        return "UNCLEAR" if re.search(r"(?<![a-z])(loss|losses|deficit)(?![a-z])", low) else "POS"
    if start < 0 or not _LOSS_ATTACHED.search(low[max(0, start - 80): start]):
        return "POS"
    tail = re.split(r"[,;]\s+(compared|versus|vs\.?|excluding|including|which|reflecting)\b", low[start:])[0]
    if _PROFIT.search(tail):
        return "UNCLEAR"  # "a loss of $0.05 to earnings of $0.02": the range crosses zero
    return "NEG"


def extract(text: str) -> list[GuidanceItem]:
    out: list[GuidanceItem] = []
    header_period: str | None = None  # "Outlook for the third quarter of fiscal 2027:" applies to the bullets below
    for s in sentences(text):
        low = s.lower()
        if NO_GUIDE.search(s):
            out.append(GuidanceItem("any", None, None, "", None, s[:600], "NO_GUIDANCE", "MEDIUM"))
            continue
        if ("outlook" in low or "as follows" in low or "guidance" in low) and not _metric(s):
            hp = PERIOD_RE.search(s)
            header_period = hp.group(0) if hp else header_period
            continue
        if not any(w in low for w in FORWARD):
            continue
        ms = _metric(s)
        if not ms:
            continue
        per = PERIOD_RE.search(s)
        period = per.group(0) if per else header_period
        if len(ms) > 1:
            out.append(GuidanceItem(ms[0], None, None, "", period, s[:600], "GUIDANCE_UNCLEAR", "LOW"))  # several metrics in one sentence
            continue
        lo, hi, unit, clean, start = _parse(ms[0], s)
        sign = _sign(ms[0], s, start)
        if sign == "UNCLEAR":
            lo = hi = None
        elif sign == "NEG" and lo is not None and hi is not None:
            lo, hi = -max(lo, hi), -min(lo, hi)
        if lo is None:
            out.append(GuidanceItem(ms[0], None, None, unit, period, s[:600], "GUIDANCE_UNCLEAR", "LOW"))
            continue
        out.append(GuidanceItem(ms[0], lo, hi, unit, period, s[:600], "EXTRACTED", "MEDIUM" if clean and period else "LOW"))
    return out
