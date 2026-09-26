# 3차 독립 평가 반례 (대상 커밋 445221f)

평가 결과: **63/100 · C 등급 · A Gate 실패** (G1 열린 P0, G2 종목 동일성 P1, G3 실제 공급자 검증 없음).
자세한 내용은 같은 폴더의 `report.html`을 보십시오.

## 실행

```bash
pip install -r backend/requirements.lock && pip install --no-deps -e ./backend
python -m pytest -q -o addopts="" evaluations/eval3      # 445221f: 19 failed (의도한 실패)

# N12 (프런트)
cp evaluations/eval3/frontend/live_status_freeze.test.tsx frontend/src/pages/
cd frontend && npx vitest run src/pages/live_status_freeze.test.tsx   # 445221f: 1 failed
```

이 테스트들은 `backend/tests` 밖에 있어 CI가 수집하지 않습니다. 고친 뒤에는 모두 통과해야 하며,
통과한 테스트는 회귀 테스트로 `backend/tests`에 옮기면 됩니다.

## 테스트와 반례 대응

| 반례 | 등급 | 테스트 | 내용 |
|---|---|---|---|
| N1 | P0 | `test_N1_*` (6) | 이익 가이던스가 무관한 "loss" 단어 때문에 음수로 저장(EXTRACTED) → BEAT_WEAK_GUIDE |
| N2 | P1 | `test_N2_*` (2) | 이미 알려진 티커로 개명하면 재사용으로만 처리, 개명한 회사는 상장폐지로 기록 |
| N3 | P1 | `test_N3_*` | 개명 후 옛 티커 재사용 → 다른 회사 분할이 개명 회사에 적용(EPS ×10), 이력 소실, 유니버스 중복 |
| N4 | P1 | `test_N4_*` | 개명 후 분할이 개명 전 봉에 적용되지 않음 → 개명일 가짜 −50% |
| N5 | P1 | `test_N5_*` | 티커 재사용 뒤 모의투자 재계산이 과거 거래를 PENDING으로 되돌리고 새 거래를 열지 않음 |
| N6 | P2 | `test_N6_*` | 파서가 못 읽은 10-Q 제출사를 준비도 게이트 분모에서 빼서 20% 수집에 SCANNER_READY |
| N7 | P2 | `test_N7_*` | 백오프 뒤 재시도가 TTL 캐시에 가로채여 요청 없이 attempts만 증가 |
| N8 | P2 | `test_N8_*` | 정정 공시(10-Q/A, 10-K/A)를 읽지 않음 |
| N9 | P2 | `test_N9_*` | 파생 4분기의 첫 값이 10-K 이전 정정을 무시 → TTM 360 (연간 380) |
| N10 | P2 | `test_N10_*` | 같은 CIK가 새 티커로 재상장하면 상장폐지 이력이 지워짐 |
| N11 | P2 | `test_N11_*` | 목록 화면은 시세 캐시가 없으면 60분 동안 가격 확인 없이 CURRENT |
| N12 | P2 | `frontend/live_status_freeze.test.tsx` | 같은 종목의 새 추천이 생기면 상세 화면 상태가 굳음 |
| N13 | P2 | `test_N13_*` | live-verify가 저장소 자료만으로 bars VERIFIED |
| N14 | P3 | `test_N14_*` | 한 티커의 세 번째 재사용에서 보관 키 충돌로 동기화가 매번 중단 |
