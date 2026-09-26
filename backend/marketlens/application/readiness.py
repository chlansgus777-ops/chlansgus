"""Is the data good enough to trust today's recommendations? (scanner readiness + recommendation gate)

- Sync status (SYNC_COMPLETE / SYNC_PARTIAL / NEVER_SYNCED) is not scanner readiness.
- SCANNER_READY needs enough price history, market caps, sector metadata and fundamentals for the
  universe the scanner actually screens. Otherwise SCANNER_NOT_READY with the missing pieces and progress
  — the UI must never present "no opportunities" when the data simply is not there yet.
- Recommendation readiness: FULL (every decision input live), LIMITED (core ready, some inputs
  unavailable/partial — always the case with free data), PAPER ONLY (MOCK data), NOT READY.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

# minimum shares of the screened universe (listed, market-cap known) before a scan is meaningful
MIN_PRICE_HISTORY = 0.90  # ≥ 60 sessions of bars
MIN_MARKET_CAP = 0.80
MIN_SECTOR = 0.80  # of large caps
MIN_FUNDAMENTALS = 0.80  # of large caps that file quarterly us-gaap XBRL (20-F filers excluded)
MIN_MARKET_DAYS = 60


@dataclass(frozen=True)
class DataCategory:
    category: str  # 가격, 일봉 이력, 재무, …
    status: str  # READY | PARTIAL | ACCUMULATING | UNAVAILABLE | BLOCKED_BY_CREDENTIAL | NOT_LIVE_VERIFIED
    provider: str
    note: str


@dataclass(frozen=True)
class Readiness:
    mode: str
    sync: dict[str, Any]
    scanner_status: str  # SCANNER_READY | SCANNER_NOT_READY | NOT_APPLICABLE
    scanner_reasons: tuple[str, ...]
    progress: dict[str, float | None]  # 0..1 per requirement
    recommendation_readiness: str  # FULL | LIMITED | PAPER ONLY | NOT READY
    readiness_reasons: tuple[str, ...]
    categories: tuple[DataCategory, ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _cfg(reg: Any, kind: str, name: str) -> bool:
    return any(p.name == name and getattr(p, "configured", True) for p in reg.chain(kind).providers)


def categories(reg: Any, stats: dict[str, Any] | None, live_verified: dict[str, bool]) -> tuple[DataCategory, ...]:
    def lv(key: str, status: str) -> str:
        return status if live_verified.get(key) or status in ("UNAVAILABLE", "BLOCKED_BY_CREDENTIAL") else f"{status} (NOT_LIVE_VERIFIED)"

    fin, poly, fred = _cfg(reg, "price", "finnhub"), _cfg(reg, "price", "polygon"), _cfg(reg, "macro", "fred")
    av = _cfg(reg, "analyst", "alphavantage")
    hist_days = (stats or {}).get("estimate_history_days", 0)
    bars_ok = bool(stats and stats["listed"] and stats["bars_60"] / stats["listed"] >= MIN_PRICE_HISTORY)
    rev_status = "ACCUMULATING" if hist_days < 90 else "PARTIAL"
    return (
        DataCategory("현재가", lv("price", "READY" if fin else "BLOCKED_BY_CREDENTIAL"), "finnhub", "무료 요금제: 실시간 보장 없음(DELAYED로 표시)"),
        DataCategory("일봉 이력", lv("bars", ("READY" if bars_ok else "PARTIAL") if poly else "BLOCKED_BY_CREDENTIAL"), "polygon", "grouped daily 일괄 적재 + 주식분할 보정"),
        DataCategory("재무(미국 GAAP)", lv("fundamentals", "READY"), "sec-edgar", "10-Q/10-K XBRL, 재작성은 공시 시점 기준 반영"),
        DataCategory("재무(해외 20-F)", lv("ifrs", "PARTIAL"), "sec-edgar", "연간 IFRS만(ANNUAL_ONLY), ADR 비율 없음 → 주당 밸류에이션 제외"),
        DataCategory("은행 지표", lv("bank", "PARTIAL"), "sec-edgar", "대출·예금·충당금·ROTCE; CET1은 태그된 경우만, NIM 없음"),
        DataCategory("애널리스트 추정치(선행 EPS)", lv("estimates", "PARTIAL" if av else "BLOCKED_BY_CREDENTIAL"), "alphavantage", "무료 하루 약 25회 → 최종 후보만"),
        DataCategory("추정치 리비전", lv("revisions", rev_status if fin else "BLOCKED_BY_CREDENTIAL"), "alphavantage+finnhub",
                     f"자체 누적 {min(hist_days, 90)}/90일" + ("" if hist_days >= 90 else " — 90일 리비전은 누적 중")),
        DataCategory("가이던스", lv("guidance", "PARTIAL"), "sec-8k", "보도자료 규칙 추출(신뢰도 낮음~보통)"),
        DataCategory("뉴스", lv("news", "READY" if fin else "BLOCKED_BY_CREDENTIAL"), "finnhub", "시장 뉴스 + 후보 종목 뉴스"),
        DataCategory("거시", lv("macro", "READY" if fred else "BLOCKED_BY_CREDENTIAL"), "fred", "ALFRED 빈티지"),
        DataCategory("공매도 잔고", lv("short_interest", "PARTIAL"), "finra", "대상 종목 범위는 실제 호출로 확인 필요"),
        DataCategory("내부자 거래", lv("insider", "PARTIAL"), "sec-form4", "최근 공시 일부"),
        DataCategory("옵션(IV·예상 변동폭)", "UNAVAILABLE", "-", "무료 공식 공급원 없음 → 반영 정도(Priced-In)는 Lite 추정"),
        DataCategory("기관 보유(13F)", "UNAVAILABLE", "-", "무료 구조화 공급원 연결 안 됨"),
    )


def evaluate(mode: str, reg: Any, stats: dict[str, Any] | None, sync_state: str | None, live_verified: dict[str, bool] | None = None) -> Readiness:
    lvf = live_verified or {}
    sync = json.loads(sync_state) if sync_state else {"status": "NEVER_SYNCED"}
    if mode == "MOCK":
        return Readiness(mode, {"status": "NOT_APPLICABLE"}, "NOT_APPLICABLE", (), {}, "PAPER ONLY",
                         ("모의(MOCK) 데이터 — 실제 시장이 아니므로 연습·검증용으로만 사용",), ())
    reasons: list[str] = []
    prog: dict[str, float | None] = {}
    if not stats or not stats.get("listed"):
        reasons.append("유니버스(종목 목록)가 아직 적재되지 않음 — 데이터 동기화를 실행하세요")
        prog = {"price_history": 0.0, "market_cap": 0.0, "sector": 0.0, "fundamentals": 0.0, "market_days": 0.0}
    else:
        listed, large = stats["listed"], max(1, stats["large"])
        prog = {
            "price_history": round(stats["bars_60"] / listed, 4),
            "long_history": round(stats["bars_240"] / listed, 4),
            "market_cap": round(stats["with_market_cap"] / listed, 4),
            "sector": round(stats["large_with_sector"] / large, 4),
            "fundamentals": round(stats["large_with_fundamentals"] / max(1, large - stats.get("large_fund_not_supported", 0)), 4),
            "market_days": round(min(1.0, stats["market_days"] / MIN_MARKET_DAYS), 4),
            "estimate_history": round(min(1.0, stats["estimate_history_days"] / 90), 4),
        }
        if stats["market_days"] < MIN_MARKET_DAYS:
            reasons.append(f"저장된 거래일 {stats['market_days']}일 < {MIN_MARKET_DAYS}일 — 스캐너 최소 요건(60거래일) 미달")
        if prog["price_history"] < MIN_PRICE_HISTORY:
            reasons.append(f"60거래일 이상 가격 이력이 있는 종목 {prog['price_history']:.0%} < {MIN_PRICE_HISTORY:.0%}")
        if prog["market_cap"] < MIN_MARKET_CAP:
            reasons.append(f"시가총액 확인 종목 {prog['market_cap']:.0%} < {MIN_MARKET_CAP:.0%}")
        if prog["sector"] < MIN_SECTOR:
            reasons.append(f"대형주 업종 정보 {prog['sector']:.0%} < {MIN_SECTOR:.0%}")
        if prog["fundamentals"] < MIN_FUNDAMENTALS:
            failed, gap = stats.get("large_fund_failed", 0), stats.get("large_fund_parse_gap", 0)
            detail = ", ".join(x for x in (f"수집 실패 {failed}종목(재시도 대기)" if failed else "", f"재무 태그 해석 불가 {gap}종목" if gap else "") if x)
            reasons.append(f"대형주 분기 재무 수집 {prog['fundamentals']:.0%} < {MIN_FUNDAMENTALS:.0%}" + (f" ({detail})" if detail else " — 동기화가 나눠서 수집 중"))
    if sync.get("status") != "SYNC_COMPLETE":
        reasons.append(f"마지막 동기화 상태: {sync.get('status')} (남은 가격 거래일 {sync.get('bar_days_remaining', '?')})")
    scanner = "SCANNER_READY" if not reasons else "SCANNER_NOT_READY"
    cats = categories(reg, stats, lvf)
    core = [c for c in cats if c.category in ("현재가", "일봉 이력", "재무(미국 GAAP)")]
    rr_reasons: list[str] = []
    if scanner != "SCANNER_READY":
        rec = "NOT READY"
        rr_reasons.append("스캐너 데이터 준비가 끝나지 않아 추천을 실전 판단에 쓰면 안 됨")
    elif any(c.status.startswith(("BLOCKED", "UNAVAILABLE")) for c in core):
        rec = "NOT READY"
        rr_reasons.append("핵심 공급자(가격·일봉·재무) 연결 안 됨")
    else:
        limited = [c.category for c in cats if not c.status.startswith("READY")]
        rec = "LIMITED" if limited else "FULL"
        if limited:
            rr_reasons.append("일부 판단 재료가 부분적이거나 없음: " + ", ".join(limited))
        unverified = [c.category for c in cats if "NOT_LIVE_VERIFIED" in c.status]
        if unverified:
            rr_reasons.append("실제 API로 아직 검증되지 않은 공급자: " + ", ".join(unverified))
    return Readiness(mode, sync, scanner, tuple(reasons), prog, rec, tuple(rr_reasons), cats)
