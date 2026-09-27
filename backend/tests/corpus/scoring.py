"""Scores the guidance extractor on the labelled corpus of REAL 8-K Exhibit 99 sentences (guidance_corpus.jsonl).

- wrong: an EXTRACTED item whose metric and range match no label of that sentence (a wrong value, a result read as
  guidance, a segment read as the company) — must be 0.
- recall: labelled company-level items that an EXTRACTED item matches, over all labelled items.
Sentences with ``expect: null`` are forms the model cannot represent (two measures of one metric in one sentence, two
table columns); they are listed but not scored."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

CORPUS = Path(__file__).with_name("guidance_corpus.jsonl")  # real 8-K Exhibit 99 sentences, labelled by hand
EVALUATORS = Path(__file__).with_name("guidance_corpus_evaluators.jsonl")  # every evaluator's guidance sentence, as written
BASELINE = Path(__file__).with_name("guidance_corpus_score.json")  # the score CI must not fall below


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= max(1e-9, 1e-6 * max(abs(a), abs(b)))


@dataclass
class Score:
    sentences: int = 0
    scored: int = 0
    expected: int = 0
    matched: int = 0
    wrong: list[tuple[str, str, float | None, float | None]] = field(default_factory=list)
    missed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def recall(self) -> float:
        return self.matched / self.expected if self.expected else 1.0


def score(path: Path = CORPUS) -> Score:
    from marketlens.domain.guidance import extract

    sc = Score()
    for line in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        sc.sentences += 1
        sign = row.get("sign")
        if row["expect"] is None and sign is None:
            continue
        sc.scored += 1
        got = [i for i in extract(row["sentence"]) if i.status == "EXTRACTED"]
        if sign is not None:  # evaluator sign cases: UNCLEAR is allowed, a value with the wrong sign is wrong
            for g in got:
                if (sign == "neg" and (g.high is None or g.high >= 0)) or (sign == "pos" and (g.low is None or g.low <= 0)):
                    sc.wrong.append((row["id"], g.metric, g.low, g.high))
            continue
        exp = row["expect"]
        for g in got:
            if not any(e["metric"] == g.metric and _same(e["low"], g.low) and _same(e["high"], g.high) for e in exp):
                sc.wrong.append((row["id"], g.metric, g.low, g.high))
        for e in exp:
            sc.expected += 1
            if any(g.metric == e["metric"] and _same(e["low"], g.low) and _same(e["high"], g.high) for g in got):
                sc.matched += 1
            else:
                sc.missed.append((row["id"], e["metric"]))
    return sc


if __name__ == "__main__":
    import sys

    for name, path in (("real 8-K", CORPUS), ("evaluators", EVALUATORS)):
        s = score(path)
        print(f"{name}: sentences {s.sentences}, scored {s.scored}, expected items {s.expected}, matched {s.matched}, recall {s.recall:.3f}, wrong {len(s.wrong)}")
        for w in s.wrong:
            print("  WRONG", w)
        if "-v" in sys.argv:
            for m in s.missed:
                print("  missed", m)
