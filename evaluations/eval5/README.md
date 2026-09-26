# 5차 독립 평가 반례 (대상 커밋 f6b048b)

평가 결과: **78/100 · B 등급 · A Gate 실패** (5개 중 4개 통과. G3 실제 공급자 검증 없음).
자세한 내용은 같은 폴더의 `report.html`을 보십시오.

## 실행

```bash
pip install -r backend/requirements.lock && pip install --no-deps -e ./backend
python -m pytest -q -o addopts="" evaluations/eval5      # f6b048b: 10 failed (의도한 실패)
```

이 테스트들은 `backend/tests` 밖에 있어 CI가 수집하지 않습니다. 고친 뒤에는 모두 통과해야 하며,
통과한 테스트는 회귀 테스트로 `backend/tests`에 옮기면 됩니다.

## 테스트와 반례 대응

| 반례 | 등급 | 테스트 | 내용 |
|---|---|---|---|
| L1 | P1 | `test_L1_*` (8) | 틀린 부호로 EXTRACTED: "net loss" 뒤에 다른 EPS 표현이 가리키는 양수 가이던스가 음수로 저장(4문장), "expects to lose"·en dash 마이너스가 양수로 저장(3문장). 끝단에서 BEAT_WEAK_GUIDE |
| L2 | P2 | `test_L2_*` | 7일 규칙이 부재 기간이 아니라 동기화 간격을 셈. 하루 빠진 날 동기화가 돌고 다음 동기화가 10일 뒤면, 계속 거래된 종목의 이력이 잘리고 상장폐지로 기록 |
| L3 | P2 | `test_L3_*` | 짝을 정하지 못한 개명을 상장폐지로 기록. 끝 글자가 같은 은퇴 클래스가 있으면 단일 개명도 연결되지 않음 |
