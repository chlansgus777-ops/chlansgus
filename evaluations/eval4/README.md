# 4차 독립 평가 반례 (대상 커밋 4182468)

평가 결과: **73/100 · C 등급 · A Gate 실패** (5개 중 3개 통과. G1 열린 P0, G3 실제 공급자 검증 없음).
자세한 내용은 같은 폴더의 `report.html`을 보십시오.

## 실행

```bash
pip install -r backend/requirements.lock && pip install --no-deps -e ./backend
python -m pytest -q -o addopts="" evaluations/eval4      # 4182468: 13 failed (의도한 실패)

# M5 (프런트)
cp evaluations/eval4/frontend/live_status_poll_failure.test.tsx frontend/src/pages/
cd frontend && npx vitest run src/pages/live_status_poll_failure.test.tsx   # 4182468: 1 failed
```

이 테스트들은 `backend/tests` 밖에 있어 CI가 수집하지 않습니다. 고친 뒤에는 모두 통과해야 하며,
통과한 테스트는 회귀 테스트로 `backend/tests`에 옮기면 됩니다.

## 테스트와 반례 대응

| 반례 | 등급 | 테스트 | 내용 |
|---|---|---|---|
| M1 | P0 | `test_M1_*` (10) | 손실 표현과 숫자 사이에 기간·기준·동사가 끼면 손실 가이던스가 양수로 저장(2차 P0 방향). 제외 손실 범위가 먼저 나오면 그 금액을 EPS로 저장. GAAP 손실·non-GAAP EPS가 한 문장이면 첫 범위를 채택. 끝단에서 GUIDE_UP |
| M2 | P2 | `test_M2_*` | 같은 티커로 재상장하면 상장폐지 이력이 지워짐(N10의 남은 경우) |
| M3 | P2 | `test_M3_*` | 옛 티커로 되돌아가는 개명(AAA→BBB→AAA)이 BBB 상장폐지로 기록되고 BBB 기간 가격이 빠짐 |
| M4 | P2 | `test_M4_*` | 한 회사의 두 클래스가 한 번에 개명되면 목록 순서에 따라 클래스 가격 이력이 서로 바뀜 |
| M5 | P3 | `frontend/live_status_poll_failure.test.tsx` | 폴링이 실패하면 상세 화면이 마지막 CURRENT를 계속 보여줌 |
