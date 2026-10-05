"""The analysis brief (application/brief.py): the five questions answered from the stored snapshot, every statement typed
and specific to the stock (product overhaul 2026-09-28)."""
from __future__ import annotations

import copy

import pytest

from marketlens.application.brief import build_brief
from marketlens.domain.decision import DecisionThresholds
from marketlens.infrastructure.db import repository as repo
from tests.integration.test_service_api import make_service

KINDS = {"FACT", "CALC", "VIEW", "ASSUME"}
# lines that are rules of the product, the same for every stock by design (they say how a number was built)
FIXED = ("목표가는 최근 가격대", "손절 기준은 지지선", "최대 매수가는 손익비", "동종 비교는 같은 업종", "분석 후 다음 거래일", "이 종목의 첫 분석")


@pytest.fixture(scope="module")
def scanned():
    svc = make_service(universe=80)
    svc.run_scan(run_committee=False)
    with svc.sf() as s:
        rows = {r.ticker: r for r in repo.all_recommendations(s)}
    return svc, rows


def _brief(svc, row, **kw):
    return build_brief(row.result, row.inputs, kw.pop("levels", svc.levels_now(row)), DecisionThresholds(), row.final_action, row.score, **kw)


def _texts(b):
    out = [i["text"] for k in ("changed", "support", "against", "triggers") for i in b[k]]
    out += [i["text"] for i in b["valuation"]["items"] + b["valuation"]["plan"]] + b["valuation"]["assumptions"] + [u["text"] for u in b["unknowns"]]
    return out


def test_every_statement_is_typed_and_the_plan_arithmetic_is_the_stored_one(scanned):
    svc, rows = scanned
    row = next(r for r in rows.values() if r.final_action == "BUY")
    b = _brief(svc, row)
    for k in ("changed", "support", "against", "triggers"):
        assert all(i["kind"] in KINDS and i["text"] for i in b[k]), k
    en = row.result["entry"]
    plan = " ".join(i["text"] for i in b["valuation"]["plan"])
    assert f"${en['stop']:,.2f}" in plan and f"${en['max_buy']:,.2f}" in plan and f"${en['target1']:,.2f}" in plan
    # the max-buy formula is printed only because it reproduces the stored number
    t1, st = en["target1"], en["stop"]
    assert abs((t1 + 2 * st) / 3 - en["max_buy"]) < 0.011 and "÷ 3" in plan
    earn = next(i for i in b["changed"] if i["label"] == "실적")
    assert earn["kind"] == "FACT" and earn["source"] and f"{row.result['earnings']['eps_surprise'] * 100:+.1f}%" in earn["text"]


def test_no_sentence_would_fit_another_stock_unchanged(scanned):
    """종목명만 바꿔도 통하는 설명 없음: across every stock of the scan, each explanatory sentence carries one of the stock's
    own numbers — except the product's fixed rule lines and the stock's registered thesis conditions."""
    svc, rows = scanned
    for row in rows.values():
        b = _brief(svc, row)
        items = [i for k in ("changed", "support", "against", "triggers") for i in b[k]] + b["valuation"]["items"] + b["valuation"]["plan"]
        generic = [i["text"] for i in items if not any(ch.isdigit() for ch in i["text"])
                   and not any(i["text"].startswith(f) for f in FIXED) and i["label"] != "투자 논리"]
        assert not generic, (row.ticker, generic)


def test_a_split_rescales_the_plan_and_says_so(scanned):
    svc, rows = scanned
    row = next(r for r in rows.values() if r.final_action == "BUY")
    lv = dict(svc.levels_now(row))
    f = 10.0
    lv = {k: (v / f if isinstance(v, float) and k != "split_factor" else v) for k, v in lv.items()} | {"split_factor": f}
    b = _brief(svc, row, levels=lv)
    plan = " ".join(i["text"] for i in b["valuation"]["plan"])
    assert f"${lv['max_buy']:,.2f}" in plan and f"${row.result['entry']['max_buy']:,.2f}" not in plan
    assert not any("ATR" in (i.get("why") or "") and str(round(row.result["entry"]["stop"], 2)) in (i.get("why") or "") for i in b["valuation"]["plan"])
    assert any("현재 주식 수 기준" in x for x in b["valuation"]["limits"])


def test_gaps_say_their_impact_and_how_to_fix_them(scanned):
    svc, rows = scanned
    row = next(iter(rows.values()))
    r = copy.deepcopy(row.result)
    r["data_quality"]["checks"].append({"data_type": "analyst", "quality": "MISSING", "reason_ko": "애널리스트 추정치: 받지 못함"})
    r["scorecard"]["components"][0]["available"] = False
    r["options"] = None
    b = build_brief(r, row.inputs, svc.levels_now(row), DecisionThresholds(), row.final_action, row.score)
    gap = next(u for u in b["unknowns"] if u["label"] == "애널리스트 추정치")
    assert gap["impact"] and "키" in gap["fix"]
    assert any("보수적" in u["impact"] for u in b["unknowns"] if u["label"] == "점수")
    assert not any(u["label"] == "옵션" for u in b["unknowns"])  # options removed 2026-09-29: never a gap to show


@pytest.mark.parametrize("action", ["BUY", "BUY SMALL", "WAIT", "WATCH", "HOLD"])
def test_triggers_name_the_levels_that_would_change_the_call(scanned, action):
    svc, rows = scanned
    row = next(r for r in rows.values() if r.final_action == "BUY")
    th = DecisionThresholds()
    lv = dict(svc.levels_now(row))
    if action == "WAIT":
        lv["price"] = lv["max_buy"] * 1.05  # waiting above the max buy price
    b = build_brief(row.result, row.inputs, lv, th, action, 74.0)
    texts = [t["text"] for t in b["triggers"]]
    assert any(f"${lv['stop']:,.2f}" in t for t in texts)  # the stop is always a trigger
    if action == "BUY":
        assert any(f"{th.buy_exit:g} 아래" in t for t in texts) and any(f"${lv['max_buy']:,.2f}를 넘으면" in t for t in texts)
    if action == "WAIT":
        assert any("이하로 내려오면" in t for t in texts)
    assert any(t["label"] == "시간" for t in b["triggers"])


def test_issue_ids_never_reach_the_reader(scanned):
    svc, rows = scanned
    row = next((r for r in rows.values() if r.result.get("issue_impacts")), None)
    if row is None:
        pytest.skip("no stock with a linked issue in this MOCK universe")
    b = _brief(svc, row)
    assert not any("ISSUE_" in i["text"] for i in b["changed"])
    titled = _brief(svc, row, issue_titles={row.result["issue_impacts"][0]["issue_id"]: "새 수출 규제 발표"})
    assert any(i["text"].startswith("새 수출 규제 발표") for i in titled["changed"])


def test_the_stock_api_carries_the_brief(scanned):
    from fastapi.testclient import TestClient

    from marketlens.api.app import create_app

    svc, rows = scanned
    t = next(r.ticker for r in rows.values() if r.final_action == "BUY")
    with TestClient(create_app(svc.settings, service=svc, run_migrations=False), headers={"X-MarketLens-Client": "t"}) as c:
        b = c.get(f"/api/stocks/{t}").json()["brief"]
    assert b["changed"] and b["support"] and b["triggers"] and b["valuation"]["plan"]


def test_no_statement_shows_an_unfilled_placeholder_or_an_internal_name(scanned):
    """A "{th.buy_enter:g}" was printed as is (a plain string where an f-string was meant), and a component's internal
    name "return_signals" appeared as a statement's label (release audit 2026-10-05)."""
    import re

    svc, rows = scanned
    for action in ("BUY", "BUY SMALL", "WAIT", "WATCH"):
        for row in [r for r in rows.values() if r.final_action == action][:3]:
            b = _brief(svc, row)
            items = [i for k in ("changed", "support", "against", "triggers") for i in b[k]] + b["valuation"]["items"] + b["valuation"]["plan"]
            for i in items:
                for field in ("text", "why", "label"):
                    v = i.get(field) or ""
                    assert "{" not in v and "}" not in v, (action, field, v)
                    assert not re.search(r"\b[a-z]+_[a-z_]+\b", v), (action, field, v)
