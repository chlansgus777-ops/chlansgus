"""CI gate for the guidance extractor (round 10 [3]): measured on every run against the labelled corpus of REAL 8-K
Exhibit 99 sentences and against every sentence an evaluator ever gave. No wrong EXTRACTED value, company-wide recall
of at least 90 %, and never below the recorded score. Rules change only by this score; evaluator sentences are added
to guidance_corpus_evaluators.jsonl, never removed."""

from __future__ import annotations

import json

from tests.corpus.scoring import BASELINE, CORPUS, EVALUATORS, score

BASE = json.loads(BASELINE.read_text(encoding="utf-8"))


def test_the_real_8k_corpus_has_no_wrong_value_and_keeps_its_recall():
    s = score(CORPUS)
    b = BASE["real_8k"]
    assert s.sentences >= 200 and s.expected == b["expected"], (s.sentences, s.expected)
    assert not s.wrong, s.wrong
    assert s.recall >= b["min_recall"], (s.recall, s.missed)
    assert s.matched >= b["matched"], f"recall fell: {s.matched} < {b['matched']} — {s.missed}"


def test_no_evaluator_sentence_is_extracted_wrong():
    s = score(EVALUATORS)
    b = BASE["evaluators"]
    assert not s.wrong, s.wrong
    assert s.matched >= b["matched"], (s.matched, s.missed)
