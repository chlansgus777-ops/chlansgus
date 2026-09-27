# 8차 독립 평가 반례 (대상 커밋 1b48217)

평가 결과: **84/100 · B 등급 · A 불합격** (Gate 5개 모두 통과, 점수가 85점에 1점 모자람).
자세한 내용은 같은 폴더의 `report.html`을 보십시오.

## 실행

```bash
pip install -r backend/requirements.lock && pip install --no-deps -e ./backend
python -m pytest -q -o addopts="" evaluations/eval8      # 1b48217: 21 failed (의도한 실패)
```

이 테스트들은 `backend/tests` 밖에 있어 CI가 수집하지 않습니다. 고친 뒤에는 모두 통과해야 하며,
통과한 테스트는 회귀 테스트로 `backend/tests`에 옮기면 됩니다. I1은 `backend/tests`의 모의 시장 도우미
(`tests.fixtures.analysis`)를 씁니다.

## 테스트와 반례 대응

| 반례 | 등급 | 테스트 | 내용 |
|---|---|---|---|
| I1 | P1 | `test_I1_*` (2) | 분할 전에 정한 손절가를 분할 뒤 일봉과 비교. 10:1 분할 뒤 보유 종목이 "종가 기준 이탈 → 매도"가 됨(분할이 없으면 HOLD). 직전 BUY 경우는 전부터 있었고, 이번에 들어온 `guard_stop`이 분할 전 손절가를 보유 기간 내내 들고 다님. 모의투자 포지션도 이 판정에 "추천 하향"으로 청산됨 |
| I2 | P2 | `test_I2_*` (15) | 구조 규칙이 아직 받는 두 형태. 금액 뒤에 변화가 적힌 문장("EPS to be $0.10 higher than last year" 등 7문장)과, 지표 앞에 부문·구성요소가 붙은 문장("data center revenue", "interest income … per share", "services gross margin" 등 7문장). 끝단: 부문 매출 줄이 전체 매출보다 먼저 나오면 BEAT_WEAK_GUIDE |
| I3 | P2 | `test_I3_*` | 장 마감 직후 동기화에서 그날 일괄 일봉이 비어 오면 그날을 "빈 날"로 영구 기록해 다시 받지 않음. 공급자가 그 시각에 빈 응답을 주는지는 확인하지 못함 |
| I4 | P3 | `test_I4_*` | 상장폐지를 기록한 날의 일봉이 나중에 채워지면(마지막 거래일) `delisted_on()`이 None이 되어 성과·모의투자가 마지막 가격으로 닫히지 않음 |
| I5 | P3 | `test_I5_*` | 설정 화면이 키를 OS 키체인에 저장해도, 예전 `.env` 줄이 남아 있으면 재시작 뒤 옛 키를 씀 |
| I6 | P3 | `test_I6_*` | 한 종목 한도를 이미 넘은 보유 종목의 ADD에 "권장 금액 $-5,000" 같은 음수 금액 문구 |
