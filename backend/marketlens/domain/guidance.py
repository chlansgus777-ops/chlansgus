"""Deterministic extraction of management guidance from an earnings-release text (SEC 8-K, Exhibit 99).

Rules (no LLM, no inference):
- A candidate sentence must contain a forward-looking word ("expect", "outlook", "guidance", …) and
  exactly one metric (revenue, EPS, gross margin, operating margin, capex).
- Only numbers written in that sentence are stored, with the sentence itself as evidence.
- Recognised forms: "$54.0 billion, plus or minus 2%", "between $1.10 and $1.20", "$3.2 to $3.4 billion",
  "73.5%, plus or minus 50 basis points", "in the range of 45% to 46%", a single "$X billion".
- Anything else about a metric in a forward-looking sentence → GUIDANCE_UNCLEAR (kept, value None).
- "does not provide guidance", "withdraw(s/n) … guidance" → NO_GUIDANCE.
- Sign (conservative — a wrong sign is worse than no value): without any loss word the number is positive;
  with a loss word it is negative only in the clear form, EPS guidance governed by "net loss" / "loss per
  (diluted) share" in the same clause ("Net loss per share for fiscal 2027 is expected to be $1.10 to
  $1.20" → -1.20..-1.10). Every other sentence with a loss word, an explicit negative number, the word
  "negative", profit wording next to a loss, or two amounts of the same metric (GAAP and non-GAAP, an
  excluded item, last year's figure) → GUIDANCE_UNCLEAR.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

FORWARD = ("expect", "outlook", "guidance", "anticipate", "forecast", "project", "we see ", "will be in the range", "is expected")
NO_GUIDE = re.compile(r"(does not|do not|will not|won't)\s+provide\s+(\w+\s+)?(guidance|outlook)|withdr[ae]w\w*\s+(its\s+|our\s+|the\s+)?(\w+\s+)?(guidance|outlook)"
                      r"|suspend\w*\s+(its\s+|our\s+|the\s+)?(\w+\s+)?(guidance|outlook)|would not be productive", re.I)
_PLUS_MINUS = re.compile(r"\+\s?/\s?-|±")
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


_PCT = r"\s*(?:%|percent(?:age points)?)"
# an amount that is a bound, not a level ("at least $90 billion", "greater than $18.25 per share")
_BOUND = re.compile(r"(at least|at or (above|below)|greater than|more than|less than|up to|no less than|no more than|not to exceed|in excess of"
                    r"|over|under|below|above|exceeding)\s*(approximately\s+|about\s+)?\$?\s?$")
# "raised by $0.10 to a range of $11.10 to $11.50", "increased from $8.50 - $8.70 to $8.80 - $8.95": the level is after "to"
_BY_TO = re.compile(r"(?<![a-z])(by|from)\s+\$?\s?" + r"\d[\d,]*(?:\.\d+)?\s*(?:billion|million|b|m|%|percent)?"
                    r"(?:\s*(?:-|–|—|to)\s*\$?\s?\d[\d,]*(?:\.\d+)?\s*(?:billion|million|b|m|%|percent)?)?"
                    r"\s+to\s+(?:a\s+|the\s+)?(?:range of\s+|range\s+)?(?:approximately\s+|about\s+)?(?=\$|\d)")


_GUIDE_NOUN = re.compile(r"(?<![a-z])(guidance|outlook|forecast|expectations?)(?![a-z])")


def _by_to(low: str, lo: int = 0, hi: int | None = None) -> re.Match[str] | None:
    """ "<metric> guidance raised by $0.10 to $11.50" / "… guidance increased from $8.50 - $8.70 to $8.80 - $8.95" inside
    [lo, hi): only when the guidance itself is what moves (a guidance noun between the metric and "by/from") —
    "dilutive to EPS by $0.05 to $0.10" is a range of a change (evaluation 6, K1)."""
    m = _BY_TO.search(low, lo, len(low) if hi is None else hi)
    if m is None:
        return None
    heads = [x.end() for _, words in METRICS for w in words for x in re.finditer(r"(?<![a-z])" + re.escape(w) + r"s?(?![a-z])", low[: m.start()])]
    return m if heads and _GUIDE_NOUN.search(low, max(heads), m.start()) else None


def by_to_start(low: str, lo: int = 0, hi: int | None = None) -> int | None:
    """Where the new level starts in "… guidance by/from <amount> to <level>" inside [lo, hi), else None."""
    m = _by_to(low, lo, hi)
    return m.end() if m else None


def _parse(metric: str, s: str, lo_pos: int = 0, hi_pos: int | None = None) -> tuple[float | None, float | None, str, bool, int]:
    """(low, high, unit, clean, start) of the amount of ``metric`` inside s[lo_pos:hi_pos] (the whole sentence by
    default; one metric's clause when a sentence names several). ``clean`` = matched one of the recognised forms
    exactly; ``start`` = where the guided number expression begins in the sentence (-1 = nothing parsed)."""
    low = s.lower()
    hi_pos = len(low) if hi_pos is None else hi_pos
    bt = by_to_start(low, lo_pos, hi_pos)
    if bt is not None:
        lo_pos = bt  # only the new level counts
    win = low[lo_pos:hi_pos]

    def at(m: re.Match[str], g: int = 0) -> int:
        return lo_pos + m.start(g)

    def bound(pos: int) -> bool:
        return bool(_BOUND.search(low[:pos]))

    if metric in ("gross_margin", "operating_margin"):
        m = re.search(_NUM + _PCT + r"\s*,?\s*plus or minus\s*" + _NUM + r"\s*basis points", win)
        if m:
            c, bp = _val(m.group(1), m.group(2)), _val(m.group(3), m.group(4))
            return (c - bp / 100) / 100, (c + bp / 100) / 100, "fraction", True, at(m)
        m = re.search(_NUM + _PCT + r"\s*,?\s*plus or minus\s*" + _NUM + _PCT, win)
        if m:  # "62.5% +/- 1.0%": percentage points
            c, pp = _val(m.group(1), m.group(2)), _val(m.group(3), m.group(4))
            return (c - pp) / 100, (c + pp) / 100, "fraction", True, at(m)
        m = re.search(r"(?:between|range of|from)?\s*" + _NUM + r"(?:\s*%|\s*percent)?\s*(?:to|and|-|–|—)\s*" + _NUM + _PCT, win)
        if m:
            if bound(at(m, 1)):
                return None, None, "fraction", False, -1
            return _val(m.group(1), m.group(2)) / 100, _val(m.group(3), m.group(4)) / 100, "fraction", True, at(m, 1)
        ms = list(re.finditer(_NUM + _PCT, win))
        if len(ms) == 1:
            if bound(at(ms[0])):
                return None, None, "fraction", False, -1
            v = _val(ms[0].group(1), ms[0].group(2)) / 100
            return v, v, "fraction", False, at(ms[0])
        return None, None, "fraction", False, -1
    unit = "USD/share" if metric == "eps" else "USD"
    scale_word = r"\s*(billion|million|thousand|b|m)?(?![a-z])"
    if metric == "eps" and re.search(r"\$\s?" + _NUM + r"\s*(billion|million|thousand|b|m)(?![a-z])", win):
        return None, None, unit, False, -1  # a dollar total next to a per-share figure: not an EPS value
    m = re.search(r"\$\s?" + _NUM + scale_word + r"\s*,?\s*plus or minus\s*" + _NUM + r"\s*%", win)
    if m:
        c = _val(m.group(1), m.group(2)) * (_SCALE.get(m.group(3) or "", 1.0) if metric != "eps" else 1.0)
        p = _val(m.group(4), m.group(5)) / 100
        return c * (1 - p), c * (1 + p), unit, True, at(m)
    m = re.search(r"\$\s?" + _NUM + scale_word + r"\s*,?\s*plus or minus\s*\$\s?" + _NUM + scale_word, win)
    if m:  # "$9.8 billion, plus or minus $300 million", "$9.66 +/- $1.00": an absolute band, each amount with its own scale
        sc_c = _SCALE.get(m.group(3) or "", 1.0) if metric != "eps" else 1.0
        sc_d = (_SCALE.get(m.group(6) or "", 0.0) or sc_c) if metric != "eps" else 1.0
        c, d = _val(m.group(1), m.group(2)) * sc_c, _val(m.group(4), m.group(5)) * sc_d
        return c - d, c + d, unit, True, at(m)
    m = re.search(r"\$\s?" + _NUM + scale_word + r"\s*(?:to|and|-|–|—)\s*\$?\s?" + _NUM + scale_word, win)
    if m:
        if bound(at(m)):
            return None, None, unit, False, -1
        if metric == "eps":
            lo_v, hi_v = _val(m.group(1), m.group(2)), _val(m.group(4), m.group(5))
        else:
            # each end keeps its own scale word ("$900 million to $1.1 billion"); a bare end takes the other one's
            # ("$3.2 to $3.4 billion") — the new evaluator's case: 900 million was read as 900 billion
            sc_hi = _SCALE.get(m.group(6) or m.group(3) or "", 1.0)
            sc_lo = _SCALE.get(m.group(3) or m.group(6) or "", 1.0)
            lo_v, hi_v = _val(m.group(1), m.group(2)) * sc_lo, _val(m.group(4), m.group(5)) * sc_hi
        if lo_v > hi_v:
            return None, None, unit, False, -1  # not a range that reads low → high: never guess
        return lo_v, hi_v, unit, True, at(m)
    ms = list(re.finditer(r"\$\s?" + _NUM + scale_word, win))
    if len(ms) == 1 and re.search(r"(range of|range to|between)\s*(approximately\s+)?$", low[: at(ms[0])]):
        return None, None, unit, False, -1  # "to the range of $14.45" with the other end cut off: an incomplete range
    if len(ms) >= 1 and bound(at(ms[0])):
        return None, None, unit, False, -1  # "at least $90 billion": a bound, not a level
    if len(ms) == 1:
        sc = _SCALE.get(ms[0].group(3) or "", 1.0) if metric != "eps" else 1.0
        v = _val(ms[0].group(1), ms[0].group(2)) * sc
        return v, v, unit, metric != "eps" and sc > 1, at(ms[0])
    return None, None, unit, False, -1


_PROFIT = re.compile(r"(?<![a-z])(profit|profitable|profitability|net income|income|earnings|break-?\s?even|positive)(?![a-z])")
# minus signs: hyphen, U+2212 minus, and the en / figure dashes typesetters use for minus ("–$0.10", "‒5%")
_MINUS = "-−–‒"
_NEG_MARK = re.compile(r"(?:^|(?<=[\s(]))[-−–‒](?=\s?\$?\s?\d)|\$\s?\(\s?\d|\(\s?\$\s?\d|(?<!or )minus\s+\$?\s?\d"
                       r"|\(\s?\d+(?:\.\d+)?\s?%\s?\)")  # accounting notation: "(3%)" is minus 3%
_RANGE_LEFT = re.compile(r"(\d|%|billion|million|thousand|\bb|\bm)\s*$")


def _has_negative_number(low: str) -> bool:
    """An explicitly negative number ("-$0.10", "$(0.10)"); a dash between two amounts
    ("$3.2 billion - $3.4 billion", "45% - 46%") is a range, not a sign."""
    for m in _NEG_MARK.finditer(low):
        if m.group(0) in _MINUS and _RANGE_LEFT.search(low[: m.start()]):
            continue
        return True
    return False


# an amount by which a metric CHANGES (a tariff / FX / acquisition impact, "decline by", "increase by") is not the
# level of that metric — such a sentence never yields a guided level (evaluation 6, K1)
_CHANGE = re.compile(r"(?<![a-z])(impact|impacts|impacted|impacting|headwinds?|tailwinds?|dilutive|accretive|dilution|accretion"
                     r"|reduce|reduces|reduced|reducing|reduction|decline|declines|declining|decrease|decreases|decreasing"
                     r"|increase|increases|increasing|(?:grow|grows|rise|rises|fall|falls|improve|improves|expand|expands|contract|contracts)\s+by)(?![a-z])")
# a loss phrase that is negated or superseded: it says what is NOT expected ("instead of the net loss we expected")
_NEGATED = re.compile(r"(?<![a-z])(instead of|contrary to|no longer|rather than|previously|prior outlook|earlier outlook|formerly)(?![a-z])")
_LOSS_WORD = re.compile(r"(?<![a-z])(loss|losses|deficit|lose|loses|losing|lost)(?![a-z])")
# the clear forms of loss guidance: "net loss (per share)", "loss per (diluted) share"
_LOSS_EPS_FORM = re.compile(r"(?<![a-z])(net\s+loss|loss\s+per\s+(diluted\s+|basic\s+)?(common\s+)?share)(?![a-z])")
# another measure named between the loss phrase and the number: the number belongs to THAT measure
# ("a GAAP net loss and non-GAAP EPS of $0.10 to $0.15", "after a net loss …, diluted EPS of $1.10")
_OTHER_MEASURE = re.compile(r"(?<![a-z])(eps|earnings|non-gaap|adjusted|income|profit|profitable|operating|ebitda|revenue|margin)(?![a-z])")
_CLAUSE_BREAK = re.compile(r"(?<![a-z])(and|while|after|before|but|then|deliver|delivering|narrow|narrowing|reduce|reducing)(?![a-z])|[;:]")
# words that put a loss into another clause than the guided number
_OTHER_CLAUSE = re.compile(r"(?<![a-z])(excluding|excludes|exclude|compared|versus|vs\.?|despite|reflecting|including|includes|which|related to|on the sale|from the sale|charge|charges|impairment|credit)(?![a-z])")
_AMOUNT = {"usd": re.compile(r"\$\s?\(?\d"), "pct": re.compile(r"\d(?:\.\d+)?\s*%")}
_JOIN = re.compile(r"^\s*(?:to|and|-|–|‒|—)\s*$")  # "or" is not a range: "$40 million, or $0.40 per share" is two amounts


# an amount that is another quantity than the guided level: a comparison, a component, a change, or another measure
# named after "and" ("compared to prior guidance of $41.2 billion", "when compared to $6.61", "at the midpoint of $44.5
# billion", "including approximately $100 million of …", "and free cash flow of $3 to $4 billion") — round 10 corpus
_OTHER_QTY = re.compile(r"(compared (to|with)|versus|vs\.?|up from|down from|midpoint of|mid-point of|(increase|decrease|benefit|impact|change|contribution) of"
                        r"|including|includes|previously|guidance of|expectations? of)\s*(approximately\s+|about\s+|an?\s+)?\$?\s?$")


def _another_measure(between: str, metric: str) -> bool:
    """ "…, and free cash flow of" / "and a full year tax rate of": another measure, not this metric, introduces the amount."""
    m = re.search(r"(?<![a-z])(and|with|on)\s+([^$%]*?)\s+(of|to be|at|to|was|is)\s*(approximately\s+|about\s+)?\$?\s?$", between)
    if not m:
        return False
    words = _EPS_HEADS if metric == "eps" else dict(METRICS)[metric]
    return bool(m.group(2).strip()) and not any(re.search(r"(?<![a-z])" + re.escape(w) + r"s?(?![a-z])", m.group(2)) for w in words)


def _number_groups(low: str, metric: str, lo: int = 0, hi: int | None = None) -> int:
    """How many separate numeric expressions of the metric's unit s[lo:hi] holds. "$1.10 to $1.20" is one;
    "a loss of $0.10 to $0.15 … EPS of $2.40 to $2.50" is two. "plus or minus 2%" belongs to its amount; a comparison,
    a component or another measure's amount (``_OTHER_QTY``) is not counted."""
    kind = "pct" if metric in ("gross_margin", "operating_margin") else "usd"
    text = low[lo: len(low) if hi is None else hi]
    text = re.sub(r"(\d)\s*percent(age points)?", r"\1%", text)
    text = re.sub(r"plus or minus\s*\$?\s?\d[\d,]*(?:\.\d+)?\s*(%|basis points|billion|million|b|m)?", " ", text)
    hits = [m.start() for m in _AMOUNT[kind].finditer(text)]
    groups, prev_end, joined = 0, None, False
    for pos in hits:
        between_raw = text[prev_end:pos] if prev_end is not None else text[:pos]
        if prev_end is not None:
            between = re.sub(r"(billion|million|thousand|\bb\b|\bm\b|per\s+(diluted\s+)?share|[\d.,$%()\s])", " ", between_raw)
            # one range joins two amounts once: a second join ("from $13.91 - $14.11 to $13.87 - $14.07") or two amounts
            # side by side (a table row "$0.00 to $0.05 $1.57 to $1.61") are separate figures (round 10 corpus)
            if _JOIN.match(between) and not joined:
                prev_end, joined = pos + 1, True
                continue
            if _OTHER_QTY.search(between_raw) or _another_measure(between_raw, metric):
                prev_end, joined = pos + 1, False
                continue
        groups += 1
        prev_end, joined = pos + 1, False
    return groups


# words that may stand between the metric and its guided amount ("revenue for the fourth quarter is expected to be
# in the range of $X", "GAAP diluted EPS of $X"). Anything else there ("EPS BY $0.10", "revenue to be UP $50 million",
# "revenue GROWTH of $2 billion", "gross margin to DECREASE 1%") means the amount is not the metric's level.
_GAP_OK = frozenset("""for the a an this that fourth first second third quarter quarters fiscal full year years of gaap non adjusted
    diluted basic is are will would be expected expect we to in range between approximately about around roughly at from projected
    anticipated forecast forecasted our its total net per share come now currently estimated target targeted guidance outlook and
    within fy on basis reported comparable attributable common stockholders shareholders holders ending ended""".split())
# guidance verbs allowed before "by/from <amount> to <level>" ("EPS guidance raised by $0.10 to …")
_GUIDE_VERBS = frozenset("raised raises raise increased increases lowered lowers reduced narrowed revised updated changed".split())
_ASIDE = re.compile(r",\s*(excluding|including|adjusted for|net of|before|after)[^,]*,")
_PERIOD_TOKEN = re.compile(r"\bq[1-4]\s?'?\s?(fy)?\s?'?\d{0,4}\b|\bfy\s?'?\d{2,4}\b|\(\s?\d{1,2}\s?\)|\*|\b(january|february|march|april|may|june|july|august|september"
                           r"|october|november|december)\b|\b\d{1,2}\b")


_EPS_HEADS = ("earnings per share", "earnings per diluted share", "earnings", "eps", "net income", "income", "net loss", "loss",
              "losses", "lose", "loss per share")
_TO_LEVEL = re.compile(r"(?<![a-z])(improve|increase|decline|decrease|grow|rise|fall|narrow|reach|total)s?\s+to\s*(approximately\s+|about\s+|between\s+|a range of\s+)?$")


# the word right before the metric may qualify it as the company's own measure; any other word names a part of it
# ("data center revenue", "services gross margin", "interest income per share") — 8th evaluation I2
# 9th evaluation (H2/H3): the words right before the metric. Company-level qualifiers are skipped leftwards; the first
# word after them must be a guidance verb, a determiner / preposition or a company / period possessive ("The Company's
# revenue", "this year's EPS") — any other word names a part of the metric ("data center net revenue", "services GAAP
# gross margin", "interest income per share") and the amount is not the company's level
_QUALIFIERS = frozenset("""total net gaap non adjusted diluted basic consolidated reported comparable annual quarterly full year
    fiscal quarter first second third fourth fy pro forma""".split())
_LEAD_OK = frozenset("""expect expects expected expecting anticipate anticipates anticipated anticipating see sees seeing
    forecast forecasts forecasted forecasting project projects projected projecting guide guides guided guiding target
    targets targeted targeting raise raises raised raising lower lowers lowered lowering reaffirm reaffirms reaffirmed
    reaffirming reiterate reiterates reiterated reiterating update updates updated updating narrow narrows narrowed narrowing
    maintain maintains maintained maintaining provide provides provided providing report reports reported reporting generate
    generates generating deliver delivers delivering achieve achieves achieving post posts posting record estimate estimates
    estimated plan plans planned smaller larger narrower wider confirm confirms confirmed confirming affirm affirms affirmed
    affirming revise revises revised revising introduce introduces introduced introducing initiate initiates initiated
    initiating issue issues issued issuing establish establishes established establishing set sets setting
    the a an our its their this that next current with and for of on to in at as is are be will would""".split())
_POSSESSIVE_OK = re.compile(r"(company|year|quarter|period|firm|group|corporation|business)'s")
# the rest of the amount after its first number ("$0.05 to $0.08 per share", "$50 million, or 5%,"): what follows it decides
# whether it is a change or a part
_AMOUNT_TAIL = re.compile(r"\s*(?:\$\s?\(?\d[\d,]*(?:\.\d+)?\)?|\(?\d[\d,]*(?:\.\d+)?\)?\s*%|\d[\d,]*(?:\.\d+)?"
                          r"|,?\s*or\s+\d[\d.]*\s*(?:%|percent)\s*,?"
                          r"|(?:a|per)\s+(?:diluted\s+)?share(?![a-z])"
                          r"|(?:billion|million|thousand|b|m|to|and|per|diluted|share|percent|approximately|about|around|roughly)(?![a-z])|[-–‒])")
# "… $0.10 higher than last year", "$50 million, or 5%, above the third quarter": the amount is a difference, not the level
_COMPARED = re.compile(r"\s*(higher|lower|more|less|greater|fewer|above|below|better|worse|up|down)(?![a-z])")
# "$500 million from the acquired business", "$1.2 billion in the Americas": a part named after the amount — unless the
# words say when ("in fiscal 2027", "for the fourth quarter") or "from continuing operations"
_PART_AFTER = re.compile(r"\s*(from|in|for)\s+([^,;.]*)")
_PERIOD_AFTER = re.compile(r"(the\s+)?((first|second|third|fourth)\s+(fiscal\s+)?quarter|(full|fiscal|calendar)[\s-]+year|fiscal|20\d\d|q[1-4]\b|fy|"
                           r"(the\s+)?(quarter|year|period|half)\b|(first|second)\s+half|(this|next|the\s+current|the\s+coming|each)\s+|continuing operations)")


def _qualified_by_part(low: str, head_start: int) -> bool:
    """The first word before the metric, after company-level qualifiers, is not a verb, determiner or possessive."""
    tail = re.split(r"[,;:.()\u2014\u2013]", low[:head_start])[-1]
    words = [part for tok in re.findall(r"[a-z0-9'&-]+", tail) for part in tok.split("-") if part]
    for w in reversed(words):
        if w in _QUALIFIERS or re.fullmatch(r"20\d\d|q[1-4]|fy\d*", w):
            continue
        return not (w in _LEAD_OK or _POSSESSIVE_OK.fullmatch(w))
    return False  # only qualifiers, at the start of a sentence or clause


def _amount_end(low: str, start: int) -> int:
    pos = start
    while (m := _AMOUNT_TAIL.match(low, pos)) and m.end() > pos:
        pos = m.end()
    return pos


def _compared_after(low: str, start: int) -> bool:
    pos = _amount_end(low, start)
    if _COMPARED.match(low, pos):
        return True
    m = _PART_AFTER.match(low, pos)
    return bool(m and not _PERIOD_AFTER.match(m.group(2).strip()))


# round 10 corpus: a forward-looking word must stand BEFORE the amount (or say "is expected" right after it), and it must
# not refer to past guidance — "revenue of $1,513 million, exceeding the midpoint of our guidance", "exceeded our
# expectations, with revenue of $1.311 billion", "compares with previous guidance of $46.30" are results or old outlooks
_CUE = re.compile(r"(?<![a-z])(expect(s|ed|ing)?|expectations?|outlook|guidance|guides?|guided|guiding|anticipat\w*|forecast\w*|project(s|ed|ing|ions?)?"
                  r"|estimat\w*|target(s|ed|ing)?|sees?|plans?|planned)(?![a-z])")
_PAST_REF = re.compile(r"(?<![a-z])(previous(ly)?|prior|original|compar(es|ed|ing)\s+(with|to)|versus|vs\.?|exceed\w*|surpass\w*|above|beat|beats"
                       r"|ahead of|in[- ]line with|landed|came in|coming in|consistent with)(?![a-z])")
_CUE_AFTER = re.compile(r"\s*,?\s*(is|are|was|were)?\s*(now\s+|currently\s+|still\s+)?(expected|forecast(ed)?|projected|anticipated|estimated|targeted)(?![a-z])")


# "$42.3 billion to $42.8 billion, compared to prior guidance of …", "…, unchanged from prior guidance": the amount is the
# NEW guidance of a bulleted outlook
_NEW_GUIDE_AFTER = re.compile(r"\s*,?\s*(compared (to|with)|up from|down from|unchanged from|versus|vs\.?)\s+(its|the|our|the company'?s)?\s*"
                              r"(prior|previous|initial|original)\s+(guidance|outlook|expectations?|forecast)")


_BASE_REF = re.compile(r"(?<![a-z])based on the following(?![a-z])")


def _forward_cue(low: str, start: int, end: int) -> bool:
    """A forward-looking word before the amount that no past-reference word precedes, or "is expected" / "compared to
    prior guidance" right after it. Amounts after "guidance is based on the following … figures" are the base-period
    figures, whatever cue came first."""
    head = low[:start]
    if _BASE_REF.search(head):
        return False
    past = _PAST_REF.search(head)
    for m in _CUE.finditer(head):
        if past is None or m.start() < past.start():
            return True
    return past is None and bool(_CUE_AFTER.match(low, end) or _NEW_GUIDE_AFTER.match(low, end))


def _governed(metric: str, low: str, start: int) -> bool:
    """Is the amount at ``start`` the metric's own level? The metric must be named BEFORE the amount, joined to it
    only by allowed words, and nothing before the amount may describe a change or an impact ("tariffs to lower EPS
    by", "a headwind to revenue of"). A structural rule rather than a list of forbidden change words (J1)."""
    if start < 0:
        return False
    # a per-share amount is EPS only when earnings / net income / a loss leads it — "per share" alone is also a
    # charge, an expense, a dividend or a deal price (J1)
    words = _EPS_HEADS if metric == "eps" else dict(METRICS)[metric]
    last = None
    for w in words:
        for m in re.finditer(r"(?<![a-z])" + re.escape(w) + r"s?(?![a-z])", low[:start]):
            if last is None or m.end() > last.end():
                last = m
    if last is None:
        return False  # the amount comes before the metric: "$0.05 off EPS", "$150 million on revenue"
    if _qualified_by_part(low, last.start()) or _compared_after(low, start):
        return False  # a segment's / component's amount, or a difference ("$0.10 higher than last year")
    bt = _by_to(low, last.end(), start + 1)
    gap = low[last.end():bt.start()] if bt is not None and bt.end() <= start else low[last.end():start]
    gap = _PERIOD_TOKEN.sub(" ", _ASIDE.sub(" ", gap))  # footnotes, "Q3'26", dates, ", excluding …," asides
    to_level = _TO_LEVEL.search(gap)  # "is expected to improve to $0.10": the new level, not a change
    head = low[:last.end() + (to_level.start() if to_level else len(gap))]
    ch = _CHANGE.search(head)
    # raising / lowering the guidance itself is not a change amount: "increasing its full-year EPS outlook to be in the
    # range of", "EPS guidance increased from $8.50 - $8.70 to …" (round 10 corpus)
    if ch and bt is None and not re.search(r"(its|our|the)\s+[^$%]*?(guidance|outlook|expectations?|forecast)", low[ch.end():start]):
        return False
    toks = re.findall(r"[a-z]+|\d+", gap[: to_level.start()] if to_level else gap)
    allowed = _GAP_OK | (_GUIDE_VERBS if bt is not None else frozenset())
    return all(t in allowed or re.fullmatch(r"20\d\d|q[1-4]", t) for t in toks)


def _sign(metric: str, s: str, start: int, window: tuple[int, int | None] = (0, None)) -> str:
    """POS | NEG | UNCLEAR for the guided number that starts at ``start``. Conservative by construction —
    a wrong sign is worse than no value:

    - no loss word in the sentence → POS (and one numeric expression only; two ranges of the same metric,
      e.g. GAAP and non-GAAP EPS, cannot be told apart → UNCLEAR);
    - a loss word present → NEG only in the clear form: EPS guidance whose one numeric expression is governed by
      "net loss" / "loss per (diluted) share" written before it in the same clause, and no profit wording;
    - anything else with a loss word, an explicit negative sign or the word "negative" → UNCLEAR."""
    low = s.lower()
    if _has_negative_number(low) or re.search(r"(?<![a-z])negative(?![a-z])", low):
        return "UNCLEAR"
    if not _governed(metric, low, start):
        return "UNCLEAR"  # a change / impact amount, or an amount not tied to the metric: not the guided level
    if not _forward_cue(low, start, _amount_end(low, start)):
        return "UNCLEAR"  # a result or an earlier outlook, not guidance (round 10 corpus)
    if _number_groups(low, metric, window[0], window[1]) > 1:
        return "UNCLEAR"  # two amounts of the same unit (GAAP and non-GAAP, an excluded item, last year's value)
    if re.search(r"(?<![a-z])respectively(?![a-z])|gaap and non-gaap|gaap and adjusted|non-gaap and gaap", low):
        return "UNCLEAR"  # "GAAP and non-GAAP margins of 73.3% and 73.5%, respectively" is two measures, not a range
    if re.search(r"(?<![a-z])break[-\s]?even(?![a-z])", low):
        return "UNCLEAR"  # "breakeven to $0.05": one end of the range is not a written number
    if not _LOSS_WORD.search(low):
        return "POS"
    if _NEGATED.search(low):
        return "UNCLEAR"  # "instead of the net loss per share we previously expected, we now expect $0.05": the loss is not the guide
    if metric != "eps" or start < 0:
        return "UNCLEAR"  # revenue / margins / capex next to a loss word: something else is being discussed
    form = None
    for m in _LOSS_EPS_FORM.finditer(low[:start]):
        form = m
    if form is None:
        return "UNCLEAR"  # a loss word that is not the clear loss-per-share form ("credit losses", "the loss on …")
    between = low[form.end():start]
    rest = low[start:]
    if _OTHER_MEASURE.search(between) or _CLAUSE_BREAK.search(between):
        return "UNCLEAR"  # the loss phrase does not govern this number
    if _OTHER_CLAUSE.search(low[: form.start()] + " " + between) or _OTHER_CLAUSE.search(rest):
        return "UNCLEAR"
    if _PROFIT.search(between + " " + rest) or _PROFIT.search(_LOSS_EPS_FORM.sub(" ", low[: form.start()])):
        return "UNCLEAR"  # "a loss of $0.05 to earnings of $0.02", or profit wording elsewhere in the sentence
    return "NEG"


def _clauses(low: str, metrics: list[str]) -> list[tuple[str, int, int]]:
    """(metric, start, end) spans of a sentence that names several metrics: each span runs from a metric's name to the
    next different metric's name ("revenue of $67 billion and capital expenditures of $50 billion"). Round 10 corpus:
    such sentences were all GUIDANCE_UNCLEAR; each clause is now judged on its own, with the whole sentence as context."""
    occ: list[tuple[int, str]] = []
    for m, words in METRICS:
        if m not in metrics:
            continue
        for w in words:
            for x in re.finditer(r"(?<![a-z])" + re.escape(w) + r"s?(?![a-z])", low):
                occ.append((x.start(), m))
    occ.sort()
    spans: list[tuple[str, int, int]] = []
    for pos, m in occ:
        if spans and spans[-1][0] == m:
            continue
        if spans:
            spans[-1] = (spans[-1][0], spans[-1][1], pos)
        spans.append((m, pos, len(low)))
    return spans


def extract(text: str) -> list[GuidanceItem]:
    out: list[GuidanceItem] = []
    header_period: str | None = None  # "Outlook for the third quarter of fiscal 2027:" applies to the bullets below
    for raw in sentences(text):
        s = _PLUS_MINUS.sub(" plus or minus ", raw)  # "$3.150 billion +/- 5%" is a range
        low = s.lower()
        if NO_GUIDE.search(s):
            out.append(GuidanceItem("any", None, None, "", None, raw[:600], "NO_GUIDANCE", "MEDIUM"))
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
        spans = [(ms[0], 0, len(low))] if len(ms) == 1 else _clauses(low, ms)
        done: set[str] = set()
        for metric, a, b in spans:
            if metric in done:
                continue  # the same metric named again in a later clause: its first clause decides
            done.add(metric)
            lo, hi, unit, clean, start = _parse(metric, s, a, b)
            sign = _sign(metric, s, start, (a, b))
            if sign == "UNCLEAR":
                lo = hi = None
            elif sign == "NEG" and lo is not None and hi is not None:
                lo, hi = -max(lo, hi), -min(lo, hi)
            if lo is None:
                out.append(GuidanceItem(metric, None, None, unit, period, raw[:600], "GUIDANCE_UNCLEAR", "LOW"))
                continue
            out.append(GuidanceItem(metric, lo, hi, unit, period, raw[:600], "EXTRACTED", "MEDIUM" if clean and period else "LOW"))
    return out
