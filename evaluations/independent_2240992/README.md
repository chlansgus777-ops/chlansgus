# MarketLens 독립 평가 반례 — 2240992

평가 결과: **70/100 · C · A Gate 실패**. 자세한 근거와 전체 개선안은 ZIP 루트의 HTML/Markdown 보고서를 보십시오.

## 설치와 실행

전체 대상 저장소가 필요합니다. 이 폴더를 저장소의 `evaluations/eval7`로 복사합니다.

```bash
pip install -r backend/requirements.lock
pip install --no-deps -e ./backend
python -m pytest -q -o addopts="" evaluations/eval7
```

별도 위치에서 실행하려면 `MARKETLENS_SOURCE_ROOT`를 저장소 절대 경로로 지정합니다. Windows PowerShell: `$env:MARKETLENS_SOURCE_ROOT = 'C:\path\to\marketlens'`.

2240992에서 **15 failed**를 직접 확인했습니다. 모두 정상 동작을 요구한 assert에서 실패합니다. 오류를 기대하여 통과시키는 테스트가 아닙니다. 제품 수정 후에는 정상 동작 요구를 유지한 채 통과해야 합니다. 테스트는 실제 API나 주문을 호출하지 않고 메모리 SQLite, MOCK 데이터, 합성 입력을 사용합니다.

기존 backend/tests/helpers.py와 MOCK 서비스 생성 함수를 일부 사용합니다. 평가일은 2026-09-27이며 실행 환경은 Python 3.12.14입니다.

## 대응표

| 테스트 접두사 | 보고서 항목 | 검증 요구 |
|---|---|---|
| test_r1_ | F01/P0 | 구간 이탈이 경미해도 현재 매수가·손익비 조건은 우회 불가 |
| test_r2_ | F01/P0 보조 | 같은 시각의 가격으로도 계획 위반을 검출 |
| test_r3_ | F03/P2 | 장후 발표 시각 이전 조회에 실적 미노출 |
| test_r4_ | F04/P1 | 각 범위 끝점의 단위 보존 또는 추출 거부 |
| test_r5_ | F05/P1 | 거래 지속 중인 일시 누락 종목을 상장폐지로 확정하지 않음 |
| test_r6_ | F06/P1 | 티커 재사용 이후 일봉은 새 회사에 귀속 |
| test_r7_ | F07/P2 | 요청한 가격 이력의 시작 구간도 확인 |
| test_r8_ | F08/P1 | 축소한 매수량도 업종 한도 내 |
| test_r9_ | F09/P1 | 다른 회계분기 컨센서스를 가이던스에 붙이지 않음 |
| test_r10_ | F02/P0 | HOLD 상태에서도 활성 손절 유지 |
| test_r11_ | F10/P1 | 다른 회사에 이전 추천 이력 미연결 |
| test_r12_ | F11/P1 | 금리 동결에서 금리 상승 방향을 만들지 않음 |
| test_r13_ | F12/P1 | 분할만으로 직전 손절 이탈을 만들지 않음 |
| test_r14_ | F13/P1 | 분할 이후 추정 EPS의 기준 일치 또는 제외 |
| test_r15_ | F14/P2 | 토큰 없는 서버의 cross-site GET이 영속 분석을 실행하지 않음 |

## 포함된 실행 증거

- counterexamples-2240992.txt: 최종 15개 반례 실행 출력
- backend-final.txt: 기존 백엔드 570 passed, 13 skipped
- previous-counterexamples.txt: 기존 평가 반례 62 passed
- frontend-verification.txt: 타입 검사·테스트 51개·production 빌드 확인 요약

E2E 13개는 Chromium 설치 실패 때문에 이번 환경에서 확인하지 못했습니다. Windows 설치와 실제 API 재검증도 이번에 수행하지 않았습니다. 기존 62개 반례와 백엔드 테스트에는 중복이 있으므로 통과 수를 독립 표본 수로 합산하지 마십시오.
