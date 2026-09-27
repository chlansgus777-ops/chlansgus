# 6차 독립 평가 반례 (대상 커밋 f80c460)

평가 결과: **83/100 · B 등급 · A 불합격** (Gate 5개 모두 통과, 점수가 85점에 못 미침).
자세한 내용은 같은 폴더의 `report.html`을 보십시오.

## 실행

```bash
pip install -r backend/requirements.lock && pip install --no-deps -e ./backend
python -m pytest -q -o addopts="" evaluations/eval6      # f80c460: 20 failed (의도한 실패)
```

이 테스트들은 `backend/tests` 밖에 있어 CI가 수집하지 않습니다. 고친 뒤에는 모두 통과해야 하며,
통과한 테스트는 회귀 테스트로 `backend/tests`에 옮기면 됩니다.

## 테스트와 반례 대응

| 반례 | 등급 | 테스트 | 내용 |
|---|---|---|---|
| K1 | P1 | `test_K1_*` (12) | 가이던스 추출. 관세·환율·인수 영향이나 "decline/increase by" 같은 변화량을 그 지표의 가이던스 수준으로 EXTRACTED(감소도 양수, 7문장). "Instead of the net loss per share … we now expect $0.05 to $0.10"을 음수로 저장(2문장). "(3%)"을 +3%로 저장. 끝단: 글머리표 개요나 GAAP·non-GAAP 문장 뒤의 관세 영향 문장만 EXTRACTED로 남아 BEAT_WEAK_GUIDE |
| K2 | P1 | `test_K2_*` (4) | 4분기 = 연간 − (1~3분기)가 10-K에서 바뀐 기준을 섞음. 연중 10:1 분할(NVIDIA 2025 회계연도 형태): 4분기 EPS −4.49, TTM EPS −2.44. 중단영업 재작성: 4분기 매출 20(실제 80), 영업이익 −4, 전년 4분기 15 |
| K3 | P2 | `test_K3_*` (2) | 매출 개념 전환과 전년 비교치. 2025-03-01 시점 보기에서 2024 1분기가 사라지고, 10-K 날짜의 4분기가 05-01에 공시된 값으로 계산됨(110, 실제 100) |
| K4 | P2 | `test_K4_*` | 기말 뒤 첫 Item 2.02 8-K(잠정 실적 사전 발표)를 실적 발표일로 봄. EPS가 실제 발표 3.5주 전에 알려진 것으로 처리됨 |
| K5 | P3 | `test_K5_*` | 9주 공백 뒤 첫 동기화가 일봉을 최신 30일만 받은 상태에서 재상장을 판정. 계속 거래된 종목의 이력이 잘리고 상장폐지로 기록 |

K2의 분할 입력값은 NVIDIA가 실제로 공시한 값(1분기 5.98 분할 전, 2·3분기 0.67·0.78, 연간 2.94, 4분기 실제 0.89)입니다.
K4는 `pair_with_releases`를 직접 부릅니다. 입력은 지금의 `earnings_release_times`가 돌려주는 목록(모든 Item 2.02 8-K)입니다.
