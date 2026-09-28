"""The analysis brief: the stored analysis answered as the five questions a reader asks (product overhaul 2026-09-28).

1. What changed recently?            → ``changed``
2. Why does it matter?                → each item's ``why``
3. What supports / argues against it? → ``support`` / ``against``
4. How was the price judged?          → ``valuation`` (multiples, the price plan's own arithmetic, assumptions, limits)
5. What is unknown, what would change the call? → ``unknowns`` / ``triggers``

Built deterministically from the stored snapshot — nothing is re-fetched or re-scored, so a stored recommendation reads
the same tomorrow — and only from THIS stock's numbers: no sentence that would fit any other ticker unchanged. Every
statement says what it is: FACT (a provider's figure), CALC (MarketLens arithmetic on facts), VIEW (a rule's reading),
ASSUME (an assumption the number rests on); sources and dates are attached where the snapshot has them. Prices are on
today's share basis when ``levels`` (Service.levels_now) is given."""

from __future__ import annotations

from typing import Any, Mapping

COMPONENT_KO = {"fundamental": "재무", "valuation": "밸류에이션", "earnings_revision": "실적·추정치", "catalyst": "촉매",
                "macro": "거시", "technical": "가격 추세", "risk": "위험", "entry_rr": "가격 계획", "issue": "이슈"}
MULTIPLE_KO = {"forward_pe": "선행 PER", "trailing_pe": "PER", "ev_ebitda": "EV/EBITDA", "ev_sales": "EV/매출", "price_sales": "PSR",
               "p_tbv": "P/TBV", "p_b": "PBR", "p_ffo": "P/FFO", "price_fcf": "P/FCF"}
RESULT_KO = {"BEAT_AND_RAISE": "예상 상회 + 가이던스 상향", "BEAT": "예상 상회", "BEAT_WEAK_GUIDE": "예상 상회했으나 가이던스 부진",
             "GUIDE_UP": "예상 부합 + 가이던스 상향", "INLINE": "예상 부합", "GUIDE_DOWN": "예상 부합했으나 가이던스 하향",
             "MISS_STRONG_GUIDE": "예상 하회했으나 가이던스 양호", "MISS": "예상 하회", "MISS_AND_LOWER": "예상 하회 + 가이던스 하향"}
DATA_KO = {"price": "현재가", "price_history": "가격 이력", "fundamentals": "재무제표", "analyst": "애널리스트 추정치", "earnings": "실적",
           "macro": "거시 지표", "news": "뉴스", "options": "옵션", "sector": "업종 분류", "short_interest": "공매도 잔고"}
# how a missing input can be supplied (free sources only)
FIX_KO = {"price": "현재가 공급자(Finnhub 키) 연결 상태를 확인하세요", "price_history": "설정 → 데이터 준비로 가격 이력을 받으세요",
          "fundamentals": "데이터 준비가 SEC 재무를 받을 때까지 기다리세요(SEC 요청자 정보 필요)",
          "analyst": "Finnhub 또는 Alpha Vantage 무료 키를 설정하면 추정치를 받습니다(하루 호출 한도 안에서 상위 후보 우선)",
          "earnings": "다음 실적 공시 후 다시 분석하세요", "macro": "FRED 무료 키를 설정하세요", "news": "Finnhub 무료 키를 설정하세요",
          "options": "무료 공급원이 없어 판단에서 제외합니다", "sector": "종목 정보(업종)가 동기화되면 자동으로 채워집니다",
          "short_interest": "FINRA 공개 자료가 갱신되면 채워집니다"}
ISSUE_KO = {"EARNINGS": "실적", "GUIDANCE": "가이던스", "AI": "AI", "REGULATION": "규제", "EXPORT_CONTROL": "수출 통제", "TARIFF": "관세",
            "GEOPOLITICS": "지정학", "MA": "인수합병", "PRODUCT": "제품", "COMPETITION": "경쟁", "SUPPLY_CHAIN": "공급망", "RATES": "금리",
            "INFLATION": "인플레이션", "OIL": "유가", "LEGAL": "소송", "ANTITRUST": "반독점", "FINANCING": "자금 조달", "DILUTION": "지분 희석",
            "BUYBACK": "자사주 매입", "MANAGEMENT": "경영진", "CYBERSECURITY": "보안 사고"}
CHANGE_WHY = {
    "earnings": "실적 점수와 이익 추정치의 기준이 새 실적으로 바뀌었습니다",
    "guidance": "다음 분기 이익 전망의 기준이 바뀌었습니다",
    "revision": "추정치의 방향은 실적·추정치 점수에 직접 들어갑니다",
    "issue": "이슈가 이 종목에 닿는 경로가 이슈 점수에 반영됩니다",
    "regime": "시장 국면이 바뀌면 거시 점수의 순풍·역풍 해석이 바뀝니다",
    "macro": "금리는 밸류에이션(이익수익률 − 금리)과 거시 점수에 들어갑니다",
    "price_zone": "매수 가능 여부는 현재가가 가격 계획 안에 있는지로 정해집니다",
    "stop": "직전 추천의 손절 기준을 종가가 깨면 매수 근거가 없어집니다",
    "rr": "손익비 2.0 미만이면 매수 조건을 통과하지 못합니다",
    "thesis": "투자 논리 철회 조건이 발생하면 매수할 수 없습니다",
    "score": "총점이 판정 경계(매수 80, 소량 72)에 얼마나 가까운지가 바뀌었습니다",
    "score_drift": "작은 변화가 쌓여 판정 경계에 가까워졌는지 봅니다",
    "component": "세부 점수 변화가 총점에 반영됐습니다",
    "price": "가격 변화는 가격 계획과 손익비를 바꿉니다",
    "action": "추천이 바뀌었습니다",
}


# ------------------------------------------------------------------ formatting
def _pct(v: Any, d: int = 1, sign: bool = True) -> str:
    return "—" if not isinstance(v, (int, float)) else f"{v * 100:+.{d}f}%" if sign else f"{v * 100:.{d}f}%"


def _pp(v: Any) -> str:
    return "—" if not isinstance(v, (int, float)) else f"{v * 100:+.1f}%p"


def _usd(v: Any) -> str:
    return "—" if not isinstance(v, (int, float)) else f"${v:,.2f}"


def _x(v: Any) -> str:
    return "—" if not isinstance(v, (int, float)) else f"{v:.1f}배"


def _item(kind: str, text: str, *, why: str | None = None, tone: str = "neutral", source: str | None = None, when: str | None = None,
          evidence: list[str] | tuple[str, ...] = (), label: str | None = None) -> dict[str, Any]:
    return {"kind": kind, "text": text, "why": why, "tone": tone, "source": source, "when": when, "evidence": list(evidence), "label": label}


def _ev(result: Mapping[str, Any], metric_prefix: str) -> list[str]:
    return [e["evidence_id"] for e in result.get("evidence") or [] if str(e.get("metric") or "").startswith(metric_prefix)][:3]


# ------------------------------------------------------------------ 1+2 what changed, and why it matters
def _issue_name(issue_id: str, titles: Mapping[str, str]) -> str:
    """The issue's headline when the market context still has it, else its category (never the internal id)."""
    if issue_id in titles:
        return titles[issue_id]
    body = issue_id.removeprefix("ISSUE_").rsplit("_", 1)[0]
    return f"{ISSUE_KO.get(body, body.replace('_', ' ').title())} 관련 이슈"


def _changed(r: Mapping[str, Any], lv: Mapping[str, Any], titles: Mapping[str, str]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    changes = r.get("changes") or []
    first = len(changes) == 1 and changes[0].get("kind") == "initial"
    if first:
        out.append(_item("FACT", "이 종목의 첫 분석이라 이전 분석과 비교할 수 없습니다", label="지난 분석 대비"))
    else:
        for c in sorted(changes, key=lambda c: (not c.get("material"), c.get("kind") != "thesis")):
            if c.get("kind") in ("agent",) or (not c.get("material") and len(out) >= 3):
                continue
            tone = "neg" if c.get("kind") in ("stop", "thesis") else "neutral"
            out.append(_item("CALC", c.get("text", ""), why=CHANGE_WHY.get(c.get("kind", "")), tone=tone, label="지난 분석 대비"))
            if len(out) >= 5:
                break
    e = r.get("earnings") or {}
    dg = r.get("digest") or {}
    if e.get("result_quality") and e.get("result_quality") != "UNKNOWN":
        rq = e["result_quality"]
        parts = [f"매출 {_pct(e.get('revenue_surprise'))}" if e.get("revenue_surprise") is not None else None,
                 f"EPS {_pct(e.get('eps_surprise'))}" if e.get("eps_surprise") is not None else None]
        guide = f", 다음 분기 매출 가이던스는 컨센서스 대비 {_pct(e.get('guide_rev_vs_cons'))}" if e.get("guide_rev_vs_cons") is not None else ""
        text = f"최근 실적: {RESULT_KO.get(rq, rq)} (예상 대비 {' · '.join(p for p in parts if p)}{guide})"
        good = rq in ("BEAT_AND_RAISE", "BEAT", "GUIDE_UP", "MISS_STRONG_GUIDE")
        why = ("이익 추정치가 올라갈 근거입니다" if good else "이익 추정치가 내려갈 수 있는 근거입니다")
        if e.get("expectation_bar") == "HIGH":
            streak = e.get("beat_streak")
            why += f" — 다만 {f'{streak}분기 연속 상회로 ' if streak else ''}시장 기대치가 높아, 다음 발표에서 평범한 상회로는 주가 반응이 약할 수 있습니다"
        out.append(_item("FACT", text, why=why, tone="pos" if good else "neg", source="실적 공시·컨센서스", when=dg.get("last_earnings_date"),
                         evidence=_ev(r, "earnings."), label="실적"))
    a = r.get("analyst") or {}
    status = (a.get("revision_status") or {})
    if a.get("eps_revision_30d") is not None and status.get("30d", "READY") == "READY":
        r30, r90, n = a.get("eps_revision_30d"), a.get("eps_revision_90d"), a.get("analyst_count")
        text = f"EPS 추정치 30일 {_pct(r30)}" + (f", 90일 {_pct(r90)}" if r90 is not None else "") + (f" (애널리스트 {n}명)" if n else "")
        fwd_pe = ((r.get("multiples") or {}).get("forward_pe"))
        why = ("추정치가 오르면 같은 주가에서 선행 PER이 낮아집니다" + (f"(현재 {_x(fwd_pe)})" if fwd_pe else "")) if r30 > 0 else \
              "추정치가 내리면 같은 주가에서도 선행 PER이 높아집니다"
        rev = a.get("revenue_revision_30d")
        if rev is not None:
            text += f" · 매출 추정치 30일 {_pct(rev)}"
        out.append(_item("FACT", text, why=why, tone="pos" if r30 > 0 else "neg" if r30 < 0 else "neutral", source=f"추정치({a.get('source') or '공급자'})",
                         when=a.get("as_of"), evidence=_ev(r, "analyst.eps_revision"), label="추정치"))
    t = r.get("technicals") or {}
    if t.get("return_1m") is not None or t.get("return_3m") is not None:
        text = f"주가 1개월 {_pct(t.get('return_1m'))}, 3개월 {_pct(t.get('return_3m'))}"
        if t.get("rs_3m") is not None:
            text += f" · 3개월 시장(SPY) 대비 {_pp(t.get('rs_3m'))}"
        price, max_buy = lv.get("price"), lv.get("max_buy")
        why = None
        if isinstance(price, (int, float)) and isinstance(max_buy, (int, float)) and max_buy > 0:
            gap = max_buy / price - 1
            why = (f"최대 매수가 {_usd(max_buy)}까지 {_pct(gap)} 남았습니다" if gap >= 0
                   else f"이미 최대 매수가 {_usd(max_buy)}를 {_pct(-gap, sign=False)} 넘었습니다 — 추격 구간")
        out.append(_item("CALC", text, why=why, source="일봉 종가", evidence=_ev(r, "tech.rs"), label="가격"))
    for imp in (r.get("issue_impacts") or [])[:3]:
        sw = next((h for h in imp.get("horizons") or [] if h.get("horizon") == "SWING"), None)
        if not sw:
            continue
        score = sw.get("impact_score") or 0.0
        path = list(imp.get("exposure_path") or [])
        why = "이 종목이 직접 언급됨" if len(path) <= 1 else f"노출 경로(시스템 해석): {' → '.join(path)}"
        out.append(_item("VIEW", f"{_issue_name(str(imp.get('issue_id')), titles)} — 2~6주 영향 {score:+.0f}점", why=why,
                         tone="pos" if score > 3 else "neg" if score < -3 else "neutral", label="이슈"))
    return out


def _specific(text: str, r: Mapping[str, Any], lv: Mapping[str, Any]) -> str:
    """The scoring engine's few fixed reason lines, restated with this stock's own numbers."""
    a = r.get("analyst") or {}
    e = r.get("earnings") or {}
    if text.startswith("EPS 추정치"):
        nums = ", ".join(f"{w} {_pct(a.get(f'eps_revision_{k}'))}" for w, k in (("30일", "30d"), ("90일", "90d")) if a.get(f"eps_revision_{k}") is not None)
        return f"{text}{f' ({nums})' if nums else ''}"
    if text.startswith("매출 추정치"):
        nums = ", ".join(f"{w} {_pct(a.get(f'revenue_revision_{k}'))}" for w, k in (("30일", "30d"), ("90일", "90d")) if a.get(f"revenue_revision_{k}") is not None)
        return f"{text}{f' ({nums})' if nums else ''}"
    if text == "애널리스트 커버리지 적음":
        return f"애널리스트 {a.get('analyst_count')}명뿐 — 추정치가 소수 의견에 좌우됨" if a.get("analyst_count") else "추정치를 내는 애널리스트가 거의 없음"
    if text == "추정치 편차 큼" and isinstance(a.get("estimate_dispersion"), (int, float)):
        return f"애널리스트 추정치 편차 {_pct(a['estimate_dispersion'], sign=False)} — 전망이 크게 엇갈림"
    if text == "가격이 매수 구간 안" and all(isinstance(lv.get(k), (int, float)) for k in ("price", "max_buy")):
        low = lv.get("acceptable_low")
        zone = f"{_usd(low)}~{_usd(lv['max_buy'])}" if isinstance(low, (int, float)) else f"최대 {_usd(lv['max_buy'])} 이하"
        return f"현재가 {_usd(lv['price'])}가 매수 구간({zone}) 안"
    if text == "다음 실적에 대한 시장 기대치가 높음":
        streak = e.get("beat_streak")
        return (f"{streak}분기 연속 예상 상회 — 다음 실적에 대한 기대치가 높아 평범한 상회로는 부족할 수 있음" if streak
                else "다음 실적에 대한 시장 기대치가 높음 — 평범한 상회로는 부족할 수 있음")
    if text.startswith("다가오는 촉매 전"):
        nxt = (r.get("event_risk") or {}).get("nearest") or {}
        days = (r.get("event_risk") or {}).get("days_until")
        if nxt.get("title"):
            return f"{text} — {nxt['title']} {days}일 앞({nxt.get('event_date')})" if days is not None else f"{text} — {nxt['title']}"
    return text


# ------------------------------------------------------------------ 3 support / against
def _support_against(r: Mapping[str, Any], lv: Mapping[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sup: list[dict[str, Any]] = []
    ag: list[dict[str, Any]] = []
    refs = r.get("reason_evidence") or {}
    comps = sorted(((r.get("scorecard") or {}).get("components") or []), key=lambda c: -(c.get("weight") or 0))
    e = r.get("earnings") or {}
    for c in comps:
        label = COMPONENT_KO.get(c.get("name", ""), c.get("name"))
        for rs in c.get("reasons") or []:
            text = _specific(rs.get("text", ""), r, lv)
            it = _item("CALC", text, tone="pos" if rs.get("sign", 0) > 0 else "neg", evidence=refs.get(rs.get("text", ""), ()), label=label)
            (sup if rs.get("sign", 0) > 0 else ag if rs.get("sign", 0) < 0 else sup).append(it)
    if e.get("expectation_bar") == "HIGH" and not any("기대치" in x["text"] for x in ag):
        ag.append(_item("VIEW", "다음 실적에 대한 시장 기대치가 높음 — 평범한 상회로는 부족할 수 있음", tone="neg", label="실적"))
    er = r.get("event_risk") or {}
    if er.get("level") in ("HIGH", "EXTREME"):
        nxt = er.get("nearest") or {}
        ag.append(_item("FACT", f"{nxt.get('event_date', '')} {nxt.get('title', '중요 일정')} ({er.get('days_until')}일 후) — 이벤트 위험 {er.get('level')}",
                        why="; ".join(er.get("reasons") or []) or None, tone="neg", source="일정", label="일정"))
    a = r.get("analyst") or {}
    if a.get("estimate_dispersion") is not None and a["estimate_dispersion"] >= 0.15:
        ag.append(_item("FACT", f"애널리스트 추정치 편차 {_pct(a['estimate_dispersion'], sign=False)} — 전망이 크게 엇갈림", tone="neg",
                        source=f"추정치({a.get('source') or '공급자'})", label="추정치"))
    if isinstance(r.get("short_interest_pct"), (int, float)) and r["short_interest_pct"] >= 0.1:
        ag.append(_item("FACT", f"공매도 잔고 비율 {_pct(r['short_interest_pct'], sign=False)} — 하락에 베팅한 물량이 많음", tone="neg", source="FINRA·SEC", label="수급"))
    for w in ((r.get("portfolio_review") or {}).get("warnings") or [])[:2]:
        ag.append(_item("CALC", w, tone="neg", label="내 포트폴리오"))
    for f, v, t in ((r.get("macro_impact") or {}).get("contributions") or []):
        if abs(v or 0) < 0.1 or any(t == x["text"] for x in sup + ag):
            continue
        (sup if v > 0 else ag).append(_item("CALC", t, tone="pos" if v > 0 else "neg", label="거시"))
    seen: set[str] = set()

    def uniq(xs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for x in xs:
            if x["text"] in seen:
                continue
            seen.add(x["text"])
            out.append(x)
        return out
    return uniq(sup)[:7], uniq(ag)[:7]


# ------------------------------------------------------------------ 4 how the price was judged
def _valuation(r: Mapping[str, Any], inputs: Mapping[str, Any] | None, lv: Mapping[str, Any], min_rr: float) -> dict[str, Any]:
    rv = r.get("relative_valuation") or {}
    mult = r.get("multiples") or {}
    items: list[dict[str, Any]] = []
    name = MULTIPLE_KO.get(rv.get("primary_multiple") or "", rv.get("primary_multiple") or "핵심 배수")
    summary = None
    if isinstance(rv.get("primary_value"), (int, float)):
        bits = [f"{name} {_x(rv['primary_value'])}"]
        if isinstance(rv.get("history_percentile"), (int, float)):
            p = rv["history_percentile"]
            bits.append(f"자기 과거 범위의 {'하단' if p <= 0.25 else '상단' if p >= 0.75 else '중간'}(백분위 {p * 100:.0f}%)")
        if isinstance(rv.get("peer_median"), (int, float)) and isinstance(rv.get("premium_to_peers"), (int, float)):
            n_peers = len((inputs or {}).get("peer_multiples") or []) if isinstance(inputs, Mapping) else 0
            bits.append(f"동종 중앙값 {_x(rv['peer_median'])}보다 {_pct(abs(rv['premium_to_peers']), 0, False)} {'낮음' if rv['premium_to_peers'] < 0 else '높음'}"
                        + (f"(같은 업종 모델 {n_peers}개 비교)" if n_peers else ""))
        summary = ", ".join(bits)
        items.append(_item("CALC", summary, source="재무제표·가격으로 계산", evidence=_ev(r, "val."), label=name))
    if isinstance(rv.get("equity_risk_spread"), (int, float)):
        items.append(_item("CALC", f"선행 이익수익률 − 미 10년물 금리 = {_pp(rv['equity_risk_spread'])}",
                           why="채권 금리 대비 주식 이익의 여유 — 낮을수록 금리 상승에 취약합니다", evidence=_ev(r, "val.rate_spread"), label="금리 대비"))
    if isinstance(rv.get("growth_adjusted"), (int, float)):
        items.append(_item("CALC", f"PEG {rv['growth_adjusted']:.2f} (PER ÷ 이익 성장률)", why="1보다 낮으면 성장 대비 싼 편으로 봅니다", label="성장 대비"))
    a = r.get("analyst") or {}
    price = lv.get("price")
    tp = a.get("target_price_consensus")
    if isinstance(tp, (int, float)) and isinstance(price, (int, float)) and price > 0:
        f = lv.get("split_factor") or 1.0
        items.append(_item("FACT", f"애널리스트 목표가 평균 {_usd(tp / f)} (현재가 대비 {_pct(tp / f / price - 1)})", why="외부 의견입니다 — MarketLens 계산에 쓰지 않습니다",
                           source=f"추정치({a.get('source') or '공급자'})", when=a.get("as_of"), label="외부 의견"))
    if mult.get("fcf_yield") is not None:
        div = f" · 배당수익률 {_pct(mult['dividend_yield'], sign=False)}" if isinstance(mult.get("dividend_yield"), (int, float)) and mult["dividend_yield"] > 0 else ""
        items.append(_item("CALC", f"FCF 수익률 {_pct(mult['fcf_yield'], sign=False)}{div}", why="시가총액 대비 한 해 동안 번 잉여현금", label="현금 창출"))

    en = r.get("entry") or {}
    plan: list[dict[str, Any]] = []
    stop, t1, mb = lv.get("stop"), lv.get("target1"), lv.get("max_buy")
    if all(isinstance(v, (int, float)) for v in (stop, t1, mb)):
        # the engine's own rule sentences carry analysis-day prices: after a split they are replaced by today's numbers
        rationale = list(en.get("rationale") or []) if (lv.get("split_factor") or 1.0) == 1.0 else []
        sup = lv.get("support_used")
        stop_rule = next((x for x in rationale if x.startswith("손절")), None)
        plan.append(_item("CALC", f"손절 기준 {_usd(stop)}", why=(stop_rule.split(":", 1)[-1].strip() if stop_rule else None)
                          or (f"지지선 {_usd(sup)} 아래에 변동성(ATR) 여유를 둔 가격" if isinstance(sup, (int, float)) else None), label="손절"))
        t_rule = next((x for x in rationale if x.startswith("1차 목표")), None)
        plan.append(_item("CALC", f"1차 목표 {_usd(t1)}", why=(t_rule.split(":", 1)[-1].strip() if t_rule else None), label="목표"))
        derived = (t1 + min_rr * stop) / (1 + min_rr)
        if abs(derived - mb) <= max(0.011, mb * 1e-4):  # show the formula only when it is the one that produced the number
            plan.append(_item("CALC", f"최대 매수가 {_usd(mb)} = (1차 목표 {_usd(t1)} + {min_rr:g} × 손절 {_usd(stop)}) ÷ {1 + min_rr:g}",
                              why=f"이 가격보다 비싸게 사면 손익비가 {min_rr:g} 아래로 내려갑니다", label="최대 매수가"))
        else:
            plan.append(_item("CALC", f"최대 매수가 {_usd(mb)}", why=f"손익비 {min_rr:g}을 지킬 수 있는 가장 높은 가격", label="최대 매수가"))
        if isinstance(price, (int, float)) and price > stop:
            rr = (t1 - price) / (price - stop)
            plan.append(_item("CALC", f"현재가 기준 손익비 = (1차 목표 − 현재가) ÷ (현재가 − 손절) = ({_usd(t1)} − {_usd(price)}) ÷ ({_usd(price)} − {_usd(stop)}) = {rr:.2f}",
                              why="계획상 위험 1에 대한 목표 이익 — 도달 확률이 아닙니다", label="손익비"))
    assumptions = [
        "목표가는 최근 가격대(저항선)에서 정한 값입니다 — 이익 전망으로 계산한 적정가치가 아닙니다",
        "손절 기준은 지지선과 최근 변동폭(ATR)으로 정했고, 장중이 아닌 종가로 판단합니다",
        f"최대 매수가는 손익비 {min_rr:g} 이상을 지킬 수 있는 가장 높은 가격입니다",
    ] if plan else []
    if rv.get("peer_median") is not None:
        assumptions.append("동종 비교는 같은 업종 모델로 이번 스캔에서 정밀 분석한 종목의 중앙값입니다 — 회계 방식 차이는 보정하지 않습니다")
    limits = []
    if (r.get("price_quality") or "") == "DELAYED":
        limits.append("현재가가 지연 시세입니다 — 실제 주문 전 증권사 호가로 다시 확인하세요")
    if (lv.get("split_factor") or 1.0) != 1.0:
        limits.append(f"분석 이후 주식 수가 바뀌어(×{lv['split_factor']:g}) 계획의 가격을 현재 주식 수 기준으로 환산했습니다 — 근거 문장 속 가격은 분석 당시 기준입니다")
    if rv.get("history_percentile") is None and summary:
        limits.append("자기 과거 배수 이력이 부족해 과거 대비 위치는 판단하지 않았습니다")
    return {"summary": summary, "items": items, "plan": plan, "assumptions": assumptions, "limits": limits}


# ------------------------------------------------------------------ 5 unknowns and what would change the call
def _unknowns(r: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for c in ((r.get("data_quality") or {}).get("checks") or []):
        q = c.get("quality")
        if q in ("FRESH", "DELAYED"):
            continue
        dt = c.get("data_type", "")
        impact = {"STALE": "오래된 값이라 판단에서 신뢰도를 낮췄습니다", "MISSING": "이 자료 없이 보수적으로 판단했습니다",
                  "CONFLICTING": "공급자끼리 값이 달라 쓰지 않았습니다"}.get(q, "판단에 제한이 있습니다")
        out.append({"text": c.get("reason_ko") or f"{DATA_KO.get(dt, dt)}: {q}", "impact": impact, "fix": FIX_KO.get(dt), "label": DATA_KO.get(dt, dt)})
    for c in ((r.get("scorecard") or {}).get("components") or []):
        if not c.get("available"):
            out.append({"text": f"{COMPONENT_KO.get(c.get('name', ''), c.get('name'))} 점수를 계산할 자료가 부족합니다",
                        "impact": "중립보다 낮은 보수적 점수를 넣었습니다 — 총점이 실제보다 낮을 수 있습니다", "fix": None, "label": "점수"})
    for key in ("fundamental_rules", "valuation_rules"):
        rs = r.get(key) or {}
        missing = [i.get("label") for i in rs.get("items") or [] if i.get("value") is None]
        if missing:
            cov = rs.get("coverage")
            out.append({"text": f"{', '.join(missing)} 계산 불가", "impact": f"나머지 항목으로 계산했습니다(반영 범위 {cov * 100:.0f}%)" if isinstance(cov, (int, float)) else "나머지 항목으로 계산했습니다",
                        "fix": None, "label": "재무" if key == "fundamental_rules" else "밸류에이션"})
    a = r.get("analyst") or {}
    for w, st in (a.get("revision_status") or {}).items():
        if isinstance(st, str) and st.startswith("ACCUMULATING"):
            out.append({"text": f"추정치 {w.replace('d', '일')} 변화: 자체 기록을 모으는 중 ({st.split(' ', 1)[-1] if ' ' in st else ''})",
                        "impact": "이 기간의 추정치 변화는 점수에 넣지 않았습니다", "fix": "매일 분석하면 기록이 쌓여 자동으로 채워집니다", "label": "추정치"})
    if r.get("sector_known") is False:
        out.append({"text": "업종 분류가 불확실합니다", "impact": "업종별 모델 대신 일반 모델을 썼고, 매수는 소량으로 제한됩니다", "fix": FIX_KO["sector"], "label": "업종"})
    if not r.get("options"):
        out.append({"text": "옵션 시장의 예상 변동폭이 없습니다", "impact": "실적 발표 전후 변동 위험은 일정만으로 판단합니다", "fix": FIX_KO["options"], "label": "옵션"})
    return out


def _triggers(r: Mapping[str, Any], lv: Mapping[str, Any], action: str, score: float | None, th: Any, ticker: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    stop, mb, price = lv.get("stop"), lv.get("max_buy"), lv.get("price")
    bullish = action in ("BUY", "BUY SMALL", "ADD")
    if isinstance(stop, (int, float)):
        out.append(_item("VIEW", f"종가가 {_usd(stop)} 아래로 마감하면", why="매수 근거가 깨집니다 — 보유 중이면 매도, 아니면 매수 중단", tone="neg", label="가격"))
    if isinstance(mb, (int, float)):
        if bullish:
            out.append(_item("VIEW", f"현재가가 {_usd(mb)}를 넘으면", why="손익비가 기준 아래로 내려가 '대기'가 됩니다(추격 매수 안 함)", tone="warn", label="가격"))
        elif action == "WAIT" and isinstance(price, (int, float)) and price > mb:
            out.append(_item("VIEW", f"가격이 {_usd(mb)} 이하로 내려오면", why="가격 조건이 다시 맞아 매수 여부를 다시 판단합니다", tone="pos", label="가격"))
    if isinstance(score, (int, float)) and th is not None:
        if action == "BUY":
            out.append(_item("VIEW", f"점수가 {th.buy_exit:g} 아래로 내려가면(현재 {score:.1f})", why="매수 판단을 해제합니다 — 한 번 내린 판단을 작은 변화로 뒤집지 않도록 진입(80)과 해제 기준을 다르게 둡니다", tone="warn", label="점수"))
        elif action == "BUY SMALL":
            out.append(_item("VIEW", f"점수가 {th.buy_small_exit:g} 아래로 내려가면(현재 {score:.1f})", why="소량 매수 판단을 해제합니다", tone="warn", label="점수"))
            out.append(_item("VIEW", f"점수가 {th.buy_enter:g} 이상으로 오르면", why="가격 조건을 충족할 때 매수로 올립니다", tone="pos", label="점수"))
        elif action in ("WAIT", "WATCH"):
            out.append(_item("VIEW", f"점수가 {th.buy_small_enter:g} 이상이면 소량 매수, {th.buy_enter:g} 이상이면 매수 후보(현재 {score:.1f})", why="가격 조건(최대 매수가·손익비)도 함께 맞아야 합니다", tone="pos", label="점수"))
        elif action == "HOLD":
            out.append(_item("VIEW", f"매도 판단 점수가 {th.hold_floor:g} 아래면 비중 축소, {th.reduce_floor:g} 아래면 매도", why="데이터가 부족한 항목은 매도 판단에서 중립으로 봅니다", tone="warn", label="점수"))
    feats = r.get("features") or {}
    for c in (r.get("thesis_conditions") or [])[:3]:
        text, m = c.get("description", ""), c.get("metric")
        v = feats.get(m) if m else None
        if isinstance(v, (int, float)):  # where the stock stands against the rule, so the line is this stock's
            text += f" (현재 {_pp(v) if 'change' in str(m) else _pct(v) if any(k in str(m) for k in ('growth', 'margin', 'yield')) else f'{v:.2f}'})"
        out.append(_item("VIEW", text, why="투자 논리 철회 — 가격과 무관하게 매수 근거가 사라집니다", tone="neg", label="투자 논리"))
    own = [e for e in (r.get("upcoming_events") or []) if ticker in (e.get("affected") or []) or ticker in str(e.get("title", ""))]
    if own:
        e = sorted(own, key=lambda e: e.get("event_date", ""))[0]
        out.append(_item("FACT", f"{e.get('event_date')} {e.get('title')}", why="발표 후에는 실적·추정치가 바뀌므로 다시 분석하세요", source="일정", label="일정"))
    out.append(_item("VIEW", "분석 후 다음 거래일 장이 마감되면 '재확인 필요', 2거래일이 지나면 만료", why="추천은 분석 시점 가격에 대한 판단입니다", label="시간"))
    return out


def build_brief(result: Mapping[str, Any], inputs: Mapping[str, Any] | None = None, levels: Mapping[str, Any] | None = None,
                thresholds: Any = None, action: str | None = None, score: float | None = None,
                issue_titles: Mapping[str, str] | None = None) -> dict[str, Any]:
    """The five-question brief of one stored analysis (see the module docstring)."""
    r = result or {}
    en = r.get("entry") or {}
    lv: dict[str, Any] = dict(levels or {})
    for k in ("ideal_entry", "max_buy", "stop", "target1", "target2", "support_used", "resistance_used"):
        lv.setdefault(k, en.get(k))
    lv.setdefault("price", r.get("price"))
    lv.setdefault("split_factor", 1.0)
    ticker = str(r.get("ticker") or "")
    act = action or (r.get("decision") or {}).get("action") or ""
    min_rr = getattr(thresholds, "min_rr", 2.0) if thresholds is not None else 2.0
    support, against = _support_against(r, lv)
    return {
        "changed": _changed(r, lv, issue_titles or {}),
        "support": support,
        "against": against,
        "valuation": _valuation(r, inputs, lv, min_rr),
        "unknowns": _unknowns(r),
        "triggers": _triggers(r, lv, act, score if score is not None else (r.get("digest") or {}).get("score"), thresholds, ticker),
        "as_of": r.get("as_of"),
        "kinds": {"FACT": "사실", "CALC": "계산", "VIEW": "해석", "ASSUME": "가정"},
    }

