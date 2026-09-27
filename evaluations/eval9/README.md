# 9차 독립 평가 반례 (대상 커밋 2b217e2)

평가 결과: **70/100 · C 등급 · A 불합격** (Gate 4/5, G1 실패: 열린 P0).
자세한 내용은 같은 폴더의 `report.html`을 보십시오.

8차 반례 I1~I6(21건)은 모두 해결됐습니다. 점수가 내려간 이유는 두 가지입니다. 이번에 새로 찾은 반례 H1~H5가 있고,
다른 독립 검토(2240992 대상, F01~F14)의 반례 가운데 9건이 2b217e2에서도 실패합니다. 그 9건은 평가자가 직접 다시 돌려
확인했습니다. 가장 무거운 것은 F02(P0)입니다. 사용자가 입력한 보유 종목처럼 앱이 매수 추천을 한 적이 없는 보유 종목은
화면에 보인 손절가 아래로 마감해도 매도 판정이 나오지 않습니다. 이 9건 가운데 8건(F01 보조 테스트 제외)은 8차 대상
1b48217에도 있었습니다. 8차 평가가 놓친 것입니다.

## 실행

```bash
pip install -r backend/requirements.lock && pip install --no-deps -e ./backend
python -m pytest -q -o addopts="" evaluations/eval9      # 2b217e2: 39 failed (의도한 실패)
python -m pytest -q -o addopts="" evaluations            # 39 failed, 107 passed (3~8차는 통과)
```

이 테스트들은 `backend/tests` 밖에 있어 CI가 수집하지 않습니다. 고친 뒤에는 모두 통과해야 하며,
통과한 테스트는 회귀 테스트로 `backend/tests`로 옮기면 됩니다. H1은 `backend/tests`의 모의 시장
도우미(`tests.fixtures.analysis`)를 씁니다. H1(저장된 추천)·H4·H5는 `tests.live_fixtures`의 HTTP 픽스처로 만든
LIVE 서비스를 씁니다(`backend/tests/integration/test_background_sync.py`와 같은 구성).

다른 독립 검토의 반례를 2b217e2에서 실행한 결과는 `independent_2240992_on_2b217e2.txt`에 있습니다(15건 중 9건 실패).
테스트 원본은 신청자 커밋 7292d51의 `evaluations/independent_2240992/`에 있습니다.

## 테스트와 반례 대응 (평가자 반례)

| 반례 | 등급 | 테스트 | 내용 |
|---|---|---|---|
| H1 | P1 | `test_H1_*` (3) | I1의 남은 경로. 분할 실행일 당일, 그날 동기화 전에 한 분석은 분할 전 기준 손절가를 가짐. 환산 구간 (분석일, 오늘]이 실행일을 빼서 다음 분석이 "종가 기준 이탈 → 매도". 저장된 추천(`levels_now`)도 분할 전 손절가·최대 매수가를 그대로 보여 줌. 스케줄러를 켜면 분할일마다 이 경우(장전부터 분석하고 장 마감 뒤에 동기화) |
| H2 | P2 | `test_H2_*` (15) | 가이던스 규칙이 아직 받는 세 형태. 금액 뒤에 붙은 일부("revenue of $500 million from the acquired business", "in the Data Center segment"), 허용 단어 앞의 부문("Data center net revenue", "Cloud segment adjusted operating margin"), 비교어가 떨어진 차이(", or 5%, higher", "or 3% above", "a share higher"). 끝단 2건: BEAT_WEAK_GUIDE(−26%, −96%) |
| H3 | P3 | `test_H3_*` (17) | 1b48217에서 맞게 추출하던 회사 전체 가이던스가 이제 UNCLEAR. "is forecasting revenue", "expects to report revenue/EPS", "The Company's revenue/EPS/gross margin", "annual revenue/EPS", "with revenue of", "this year's revenue", "updating/maintaining revenue guidance" 등. 값이 비는 쪽(틀린 값 아님) |
| H4 | P2 | `test_H4_*` (2) | 분할 전에 입력한 보유 수량·평단을 분할 뒤 가격과 곱함. 10주 × 18 = 180(실제 100주, 1,800), 평가손익 −90%, 비중 1.8%(실제 15%). 한 종목 한도를 넘은 보유에 ADD 14주를 권함 |
| H5 | P3 | `test_H5_*` (2) | 백그라운드 데이터 준비. `sync_market()`(POST /api/sync, CLI, 스케줄러)은 작업의 잠금을 보지 않아, 작업 중에 다른 동기화가 돌면 같은 거래일 30개를 두 번 받음. 마지막 상태 저장이 실패하면(예: database is locked) 잠금이 풀리지 않아 앱을 다시 켤 때까지 "받는 중"과 비활성 버튼 |

## 다른 독립 검토에서 2b217e2에 남은 결함 (평가자가 재현하고 등급을 다시 매김)

| 항목 | 이 평가의 등급 | 재현 | 내용 |
|---|---|---|---|
| F02 | P0 | `test_r10` 실패, 평가자 탐침으로도 확인 | 직전 추천이 HOLD/REDUCE이고 이어받은 손절가가 없으면 종가 손절 점검을 하지 않음. 사용자가 입력한 보유 종목은 앱이 BUY/ADD를 낸 적이 없으면 손절 점검이 한 번도 적용되지 않음(첫 분석 HOLD, 손절 350.26 → 다음 종가 332.75 → HOLD). 화면은 "종가가 손절 기준가 아래로 마감하면 매도(보유 중)"라고 안내함 |
| F06 | P2 | `test_r6` 실패 | 티커 재사용 때 같은 동기화에서 먼저 저장한 새 회사의 첫 일봉이 옛 회사 보관 기록으로 옮겨짐 |
| F10 | P2 | `test_r11` 실패 | 티커 재사용 뒤 새 회사 분석이 옛 회사의 직전 추천(손절가·히스테리시스)을 이어받음 |
| F14 | P2 | `test_r15` 실패 | API 토큰이 없으면 다른 사이트가 시작한 GET(`/api/stocks/{t}?refresh=true`)이 분석을 저장하고, BUY면 모의투자 포지션도 만듦 |
| F07 | P3 | `test_r7` 실패 | 저장소가 끝 날짜만 덮으면 시작 구간을 확인하지 않음. 동기화 창(300일)이 분석 창(420일)보다 짧아 새 설치에서는 몇 달 동안 52주 값이 비어 있음 |
| F08 | P3 | `test_r8` 실패 | 업종 한도까지 0.5%p 남았는데 SMALL(1.25%)을 허용 |
| F09 | P3 | `test_r9` 실패 | 가이던스가 적은 분기가 아니라 가장 가까운 다음 발표의 컨센서스와 비교됨 |
| F11 | P3 | `test_r12` 실패 | "Fed leaves rates unchanged"를 금리 상승(+1, 중요도 0.7 = 중대 이슈)으로 처리. 점수 영향은 약 0.2점, 재검증 표시 |
| F01 보조 | 감점 없음 | `test_r2` 실패 | 같은 시각 호가로는 계획을 다시 검사하지 않음. 2b217e2에서는 자기 가격에서 깨진 BUY가 만들어지는 경로를 찾지 못함(r1 통과, 위원회는 낮추기만 함) |
