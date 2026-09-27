# 7차 독립 평가 반례 (대상 커밋 2240992)

평가 결과: **84/100 · B 등급 · A 불합격** (Gate 5개 모두 통과, 점수가 85점에 1점 모자람).
자세한 내용은 같은 폴더의 `report.html`을 보십시오.

## 실행

```bash
pip install -r backend/requirements.lock && pip install --no-deps -e ./backend
python -m pytest -q -o addopts="" evaluations/eval7      # 2240992: 24 failed (의도한 실패)
```

이 테스트들은 `backend/tests` 밖에 있어 CI가 수집하지 않습니다. 고친 뒤에는 모두 통과해야 하며,
통과한 테스트는 회귀 테스트로 `backend/tests`에 옮기면 됩니다.

## 테스트와 반례 대응

| 반례 | 등급 | 테스트 | 내용 |
|---|---|---|---|
| J1 | P1 | `test_J1_*` (19) | K1과 같은 부류가 남음. 변화 단어 목록 밖의 동사("lower … by", "cost $0.10 per share", "a $0.10 per share hit", "weigh on", "drag", "shave … off", "add … to", "contribute", "be up/down", "growth of", "pressure")로 쓴 변화량을 수준으로 EXTRACTED(12문장). 구조조정 비용·주식보상·상각·배당·인수 가격 같은 주당 금액을 EPS 가이던스로 EXTRACTED(5문장). 끝단 2건: BEAT_WEAK_GUIDE |
| J2 | P2 | `test_J2_*` | 합병으로 가중 희석주식수가 약 2배가 되면 `share_basis`가 2:1 분할로 읽어 4분기 EPS 1.50(실제 1.00), TTM EPS 4.50(10-K의 연간 EPS 4.00) |
| J3 | P2 | `test_J3_*` | 매출 개념 전환 뒤, 먼저 보고한 개념이 기간을 갖는 규칙 때문에 전년 대비 성장률이 두 정의를 섞음. −1%(회사 기준 +10%) |
| J4 | P3 | `test_J4_*` | 재작성 판정이 최초 값과 비교해, 이전 재작성 값을 그대로 다시 실은 다음 해 10-K도 재작성으로 봄. 4분기가 빔(실제 90) |
| J5 | P2 | `test_J5_*` | 중단영업 재작성과 매출 개념 전환이 같은 10-K에 있으면 재작성을 못 봄. 4분기 매출 20(K2 (b)와 같은 오류) |
| J6 | P3 | `test_J6_*` | 늦게 제출하는 회사(NT 10-K). 4분기 발표와 10-K가 4월 중순에 나오면, 10-K가 1분기 창을 닫아 4분기 발표가 1분기 발표일이 됨. 1분기 EPS가 4주 일찍 보임 |

`f80c460`에서 이 중 4건(J1 끝단의 매출 형식, J2, J3, J4)은 통과합니다. 이번 라운드의 수정이 만든 회귀입니다.
