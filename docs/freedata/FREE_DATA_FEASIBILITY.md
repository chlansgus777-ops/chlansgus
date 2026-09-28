# 무료 데이터 확보·검증 — 실측 현황

기준일: 2026-09-28. 지시서: 소유자 제공 "MarketLens 무료 데이터 확보·검증 지시서".

**원칙**
- 신규 구독, 결제 수단 등록, 다중 키, 한도 회피는 하지 않습니다.
- 비밀값은 출력하지 않습니다. 키는 "set" / "not set"만 기록합니다.

**판정**
- **VERIFIED:** 실제 계정의 실제 응답으로 확인했습니다.
- **PARTIAL:** 일부만 확인했습니다.
- **BLOCKED / UNVERIFIED:** 확보하지 못했거나 문서만 확인했습니다.

## 키 상태 (저장소 시크릿, 2026-09-28 run 36370036672)

| 키 | 상태 |
|---|---|
| POLYGON_API_KEY | set |
| SEC_USER_AGENT | set |
| FRED_API_KEY | set |
| ALPHAVANTAGE_API_KEY | set |
| APCA_API_KEY_ID / APCA_API_SECRET_KEY (Alpaca) | **not set** |
| FINRA_API_KEY / FINRA_API_SECRET | **not set** |

## 공급원별

| 공급원 | 판정 | 실제 확인한 것 (run) | 빠진 것 |
|---|---|---|---|
| Polygon/Massive grouped daily (원가격) | **PARTIAL** | 2024-09 경계부터 전체 시장의 일봉. 3년 전 요청은 403 "past historical entitlements" (probe run 36324251338) | 약 2년 이전 |
| **Alpaca historical SIP (raw)** | **BLOCKED** | 키 없음 → 요청 안 함 (36370036672) | 2016년 이후 가격 전부. 무료 Basic 계정 키 필요 |
| Alpha Vantage LISTING_STATUS, 2016-01-04 active | **VERIFIED** (그 날짜) | HTTP 200 CSV 8,165행 (Stock 5,766 / ETF 2,399). 헤더 symbol, name, exchange, assetType, ipoDate, delistingDate, status (36370036672) | CIK 없음. 월중 변경은 알 수 없음 |
| Alpha Vantage LISTING_STATUS, 2016-01-04 delisted | **FAILED** | HTTP 200인데 본문이 `{}` — 성공으로 치지 않음 (36370036672) | — |
| Alpha Vantage LISTING_STATUS, delisted 전체(날짜 없음) | **VERIFIED** | CSV 9,513행 (Stock 7,514 / ETF 1,999). 상장폐지일 1997-04-01~2026-09-25 (36370276901) | CIK 없음. 합병 대가·OTC 이전 정보 없음 |
| Nasdaq Trader symbol directory | **VERIFIED** (현재 목록만) | nasdaqlisted 5,636행, otherlisted 7,652행. ETF 표시·종목명 포함 (36370036672). 앱의 증권 종류 판정에 사용 | 과거 이력은 아님. 매일 보존해야 함 |
| FINRA daily short-sale **volume** (CNMS) | **VERIFIED** (표본 날짜) | 2018-08-01 7,677행, 2018-08-02, 2019-01-02, 2024-01-02 (36370036672) | 공매도 **잔고**와는 다른 자료 |
| FINRA short **interest** API | **BLOCKED** | FINRA 키 없음 | 전부 |
| SEC companyfacts / submissions | **VERIFIED** | 2009년 이후 공시 fact와 accession·acceptance 시각 (probe 36324251338). 대형주 2,323개 중 1,954개 분기 해석 성공, 369개 실패 중 347개는 외국 발행사 (진단 36369154779) | 외국 발행사의 IFRS 분기 자료 |
| SEC 발행주식수 frames | **VERIFIED** | 대표 보통주 중 dei 4분기 78.2%, dei 8분기 + us-gaap CSO 83.0% (36370230494) | 약 17% |
| FRED/ALFRED | **VERIFIED** | VIXCLS 1990~, DGS10 1962~, DTB3 1954~, DEXKOUS 1981~, CPI 첫 빈티지 1972 (36324251338) | 발표 시각(시:분) |

## 다음 순서

1. **Alpaca 키:** 소유자가 무료 Basic 계정을 만들고 두 키를 저장소 시크릿에 넣으면, 2016년 이후 SIP raw 일봉과 경계 사례 30개(`scripts/freedata/probe_free_sources.py`의 CASES)를 실측합니다.
2. **Alpha Vantage 무료 한도(일 25회) 배분:** 앱의 컨센서스 수집과 같은 키를 쓰므로 목록 조회는 하루 몇 회로 제한합니다.
3. **백테스트 판정:** 확인된 범위만 수집기에 넣습니다. 그 전까지 백테스트 판정은 "부족(예비)"입니다(`docs/backtest`).
