"""Round 10 [1]: for each defect class, the one-sentence root-cause rule, the common function that owns it and a grep of
every path in backend/marketlens that uses the rule. Writes docs/review/R10_ROOT_CAUSES.md (run from the repo root)."""

from __future__ import annotations

import subprocess
from pathlib import Path

ITEMS = [
    ("F02 (P0)", "보유 중인 종목은 종가가 화면에 보인 손절가 아래로 마감하면, 직전 판단이 무엇이든(HOLD·REDUCE·WAIT·없음 포함) 매도 판정을 낸다.",
     "pipeline.watched_stop(감시 손절가) + decision.decide()의 prior_stop_breached", r"watched_stop|prior_stop_breached|stop_breached"),
    ("H1 (P1) · H4 (P2) · F12 · F13", "주당 값(가격 수준·손절가·수량·평단·EPS·추정치)은 기록 당시 반영된 분할과 오늘 일봉에 반영된 분할의 차이만큼, 한 함수(share_multiplier)로만 환산한다.",
     "corporate_actions.share_multiplier (split_factor·analysis_basis는 이것을 부르는 얇은 포장)", r"share_multiplier\(|split_factor\(|adjust_shares\(|split_factor_since|split_adjusted"),
    ("H4 구조 해결 (기능 a)", "거래 기록이 있는 회사의 보유는 거래 기록과 저장된 분할에서 한 함수(domain.ledger.positions)로만 계산하고, 보유를 쓰는 모든 곳은 Service.portfolio 하나를 거친다.",
     "domain.ledger.positions / Service.portfolio", r"\.portfolio\(|repo\.holdings\(|positions\("),
    ("H2 (P2) · H3 (P3)", "가이던스는 한 절 안에 전망 신호·지표·기간·범위가 모두 있을 때만 EXTRACTED로 추출하며, 규칙은 말뭉치 점수로만 바꾼다.",
     "domain.guidance.extract (절 단위 _clauses → _parse), 채점 backend/tests/corpus/scoring.py", r"guidance\.extract\(|from marketlens\.domain\.guidance import|def extract\("),
    ("H5 (P3)", "상태를 바꾸는 작업(동기화·스캔·거래 기록 변경)은 잠금 하나로 직렬화하고, 모든 종료 경로(예외 포함)에서 잠금을 푼다.",
     "Service._lock / _sync_lock / _sync_run / _ledger_lock", r"_sync_run|_sync_lock|_ledger_lock|self\._lock\b"),
    ("F06 (P2)", "티커 재사용 아카이브는 재사용 효력일 전의 기록만 옛 회사로 옮긴다.", "MarketStore._archive_reused", r"_archive_reused|archived_as"),
    ("F07 (P3)", "저장소가 요청 구간의 시작을 덮지 못하면 그 응답을 완전한 이력으로 취급하지 않는다.",
     "DataAccess.bars(fill_gaps) + bars_backfill_complete", r"fill_gaps|bars_backfill_complete|def bars\("),
    ("F08 (P3)", "매수 크기는 종목·업종·테마·현금 한도의 남은 금액 중 가장 작은 값을 넘지 않는다(축소 뒤에도 다시 검사).",
     "domain.portfolio.review_candidate(fitting) → position_plan", r"fitting|position_plan\(|review_candidate\("),
    ("F09 (P3)", "가이던스는 같은 회계분기의 컨센서스와만 비교한다.", "estimate_book._named_quarter / _fiscal_quarter", r"_named_quarter|_fiscal_quarter|attach_guidance"),
    ("F10 (P2) · 라운드 10 리뷰 P1(주식 종류 병합)", "'같은 종목인가'는 security_id(티커, 날짜) 하나로만 판단한다 — 저장 키의 이름 변경·재상장 계보. 재사용은 끊고, 같은 CIK의 다른 주식 종류는 다른 종목(추천 이력·직전 판단·모의투자·거래 기록·수동 보유).",
     "MarketStore.security_id / security_ids → DataAccess.security_of / securities_of → company_recommendations / ledger_securities",
     r"security_id\(|security_ids\(|security_of\(|securities_of\(|company_recommendations\(|identity_on\(|ledger_securities\(|company_id\("),
    ("F11 (P3)", "방향을 말하는 단어가 없으면 거시 이슈의 방향은 0(중립)이다.", "issue_engine._move", r"_move\(|direction ="),
    ("F14 (P2)", "저장하거나 상태를 바꾸는 요청은 GET을 포함해 모두 교차 사이트 방어(클라이언트 헤더·Fetch Metadata·Origin) 뒤에서만 실행된다.",
     "api/app.py local_guard + routes.stock의 쓰기 조건", r"CLIENT_HEADER|x-marketlens-client|sec-fetch-site|UNSAFE_METHODS"),
]


def main() -> None:
    out = ["# 라운드 10 — 결함 부류별 근본 규칙과 적용 경로 grep", "",
           "각 항목: 근본 규칙 한 문장, 규칙을 담당하는 공통 함수, 그 규칙을 쓰는 모든 경로의 grep 결과(`backend/marketlens`, 테스트 제외). "
           "`python scripts/review/root_cause_greps.py`로 다시 만들 수 있습니다.", ""]
    for name, rule, fn, pat in ITEMS:
        r = subprocess.run(["grep", "-rnE", pat, "backend/marketlens", "--include=*.py"], capture_output=True, text=True, check=False).stdout.strip()
        out += [f"## {name}", "", f"**규칙:** {rule}", "", f"**공통 함수:** {fn}", "", "```", f"$ grep -rnE '{pat}' backend/marketlens --include=*.py", r, "```", ""]
    Path("docs/review/R10_ROOT_CAUSES.md").write_text("\n".join(out), encoding="utf-8")


if __name__ == "__main__":
    main()
