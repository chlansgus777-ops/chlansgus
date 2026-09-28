# 2026-09-28 독립 검토(F01–F10) 대응 기록

검토자 반례는 수정 없이 옮겼습니다.

- 백엔드: `backend/tests/acceptance_a_grade/test_independent_review_0928.py` (이 폴더의 `test_review_checks.py`와 동일)
- 화면: `frontend/review/counterexamples.test.tsx` (이 폴더의 `counterexamples.test.tsx`와 동일)

수정 전(79b535e)에서는 12개 모두 실패했고, 수정 후 12개 모두 통과합니다.

## 공통 원인

같은 의미를 가진 값이 여러 곳에서 따로 계산되고 있었습니다.

- 규모 제한은 `size_limit`, `size_cap`, `size_class` 세 이름으로 흩어져 있었습니다.
- 가격 기준은 분석 스냅샷과 현재 주식 수 기준이 섞여 있었습니다.
- 실행 가능 여부는 서버와 화면이 각자 판단했습니다.
- 비밀값 제거 규칙이 로그와 상태 기록에서 서로 달랐습니다.

그래서 값이 계산 → 저장 → API → 화면 → 모의투자로 이어지는 도중에 의미가 바뀌었습니다. 이번 수정은 조건문을 하나씩 덧붙이는 대신, 각 의미마다 계산을 한 곳으로 모았습니다.

| 의미 | 한 곳 | 쓰는 곳 |
|---|---|---|
| 최종 규모 제한 | `domain/sizing.py`의 `tightest`, `effective_size`, `recommendation_size_cap` | decide, 저장된 `size_class`, 매수 금액(API), AI 위원회, 모의투자 금액 |
| 현재 주식 수 기준 가격 | `levels_now`(모든 가격 수준) → API `recommendation` → 화면 `planNow` | 타일, 가격 구간, 사다리, 차트 선과 종가, 문장, 시나리오 |
| 지금 실행 가능한가 | `actionable_now`(서버), 화면 `quantityShown` | 매수 수량 표시(서버와 화면 모두 확인) |
| 비밀값 제거 | `infrastructure/logging.redact_text`(설정된 키 + 형태 규칙) | 로그, `/api/health`, 공급자 체인, 동기화 기록, 수집 기록, HTTP 오류 상세 |

## 항목별

| ID | 원인 | 수정 | 추가 검증 |
|---|---|---|---|
| F01 | 행동을 바꾼 제한만 `size_limit`에 기록됐습니다. | 매수 행동이면 모든 제한 중 가장 작은 값을 기록합니다. API는 `result.decision.size_limit`와 `size_class` 중 작은 값을 씁니다. 모의투자도 같은 값으로 금액을 정합니다. | `tests/invariants/test_size_limit_contract.py`: 매수 행동 3종 × 제한 4종의 화면·AI·모의투자 조합 40건. 수정을 되돌리는 3가지 변경을 모두 검출합니다. |
| F02 | 보유 종목 업종을 `last_scan_context`에서만 읽었습니다. | context가 없으면 저장된 종목 정보(LIVE: security master, MOCK: 유니버스)에서 읽습니다. 업종을 모르는 보유 종목은 후보와 같은 업종으로 가정합니다(최악의 경우). | 재시작 직후 업종 유지, 업종 미확인 보유 종목의 한도 반영 |
| F03 | 상세 화면이 `analysis.entry`(분석 당시 가격)를 그대로 썼습니다. | API가 모든 가격 수준과 차트 종가를 현재 주식 수 기준으로 보냅니다. 화면의 모든 가격 표시가 같은 계획을 씁니다. 분석 당시 가격은 '분석 당시 기록'으로 따로 보여줍니다. | 정분할·역분할 API 테스트, 정분할·역분할 화면 테스트 |
| F04 | 수량 계산이 추천의 현재 유효성을 보지 않았습니다. | 서버는 `actionable_now`가 아니면 `available=False`와 이유를 돌려줍니다. 화면은 1분마다 다시 확인한 상태가 CURRENT가 아니면 수량을 숨깁니다. | AGING, PLAN_INVALIDATED, NEEDS_REVALIDATION 화면 테스트 |
| F05 | AI 포트폴리오 매니저 의견을 BUY에만 적용했습니다. | 모든 매수 행동에 적용합니다. WATCH이면 BUY와 BUY SMALL은 WATCH, ADD는 HOLD가 됩니다. 규모는 `size_class`로 전달됩니다. | 매수 행동 3종 × 의견 4종 |
| F06 | 공용 context를 없을 때만 새로 넣었습니다. | 스캔이나 개별 분석이 성공하면 더 새로운 context로 통째로 교체합니다. 이전 context의 기업 뉴스 중 뉴스 기간 안의 것은 이어 붙입니다. 이슈 화면은 30분이 지난 context를 다시 만듭니다. | 오래된 context가 새것을 덮지 않음, 30분 뒤 재생성 |
| F07 | 스캔만 동기화를 확인했고, 확인과 획득이 원자적이지 않았습니다. | 하나의 guard 아래에서 양방향으로 확인하고 잠급니다. 직접 동기화(API, CLI, 스케줄러)도 스캔 중이면 거절합니다. | 역순 시작, 동시 시작 20회 |
| F08 | 식별 필터 전에 모든 행을 ORM으로 읽었습니다. | 가벼운 열(id, ticker, as_of)로 식별을 먼저 거른 뒤, 필요한 개수만 전체 행으로 읽습니다. 식별 필터는 제한보다 먼저 적용합니다. | 검토자 측정 스크립트(같은 기계, 메모리 SQLite, 분석 100개): ORM 로드 100 → 1개, 0.27초 → 0.005초 |
| F09 | HTTP 상세의 정규식이 따옴표 붙은 키 이름을 놓쳤습니다. 상태 기록에는 정화 규칙이 없었습니다. | 프로세스 전체가 하나의 정화기를 씁니다. 따옴표 붙은 JSON 키도 제거하고, 설정된 키 값은 길이와 무관하게 제거합니다. 상태, 체인 오류, 동기화 기록, 수집 기록에 모두 적용합니다. | 설정된 짧은 키, 따옴표 키, 카운터 오탐 없음 |
| F10 | `missing_prices`를 바로 다음 줄에서 다시 계산해 덮어썼습니다. | 누락 목록을 보존합니다. `valuation_status`(EMPTY, COMPLETE, PARTIAL, UNAVAILABLE)를 API에 싣습니다. 평가 불가 계좌에는 총액, 손익, 분산 대신 '평가 불가'를 표시하고 매수 금액도 계산하지 않습니다. | 상태 4가지, 평가 불가 계좌의 매수 금액 거절 |

## 내 기존 테스트의 변경

`backend/tests/integration/test_transactions.py::test_the_buy_amount_counts_the_holding_from_the_records`는 직접 만든 요약(가격과 손절만 있음)을 쓰던 입력을 API가 실제로 넘기는 요약(`_row_summary`)으로 바꿨습니다. F04 이후 수량은 현재 유효한 추천에만 나오기 때문입니다. 검증 내용은 그대로이며, 요약이 실행 가능하다는 확인을 하나 더했습니다.
