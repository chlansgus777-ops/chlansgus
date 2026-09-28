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
| APCA_API_KEY_ID / APCA_API_SECRET_KEY (Alpaca) | set (2026-09-28 02:42부터) |
| FINRA_API_KEY / FINRA_API_SECRET | **not set** |

## 공급원별

| 공급원 | 판정 | 실제 확인한 것 (run) | 빠진 것 |
|---|---|---|---|
| Polygon/Massive grouped daily (원가격) | **PARTIAL** | 2024-09 경계부터 전체 시장의 일봉. 3년 전 요청은 403 "past historical entitlements" (probe run 36324251338) | 약 2년 이전 |
| **Alpaca historical SIP (raw)** | **PARTIAL** | 3종목 2016-01-04~2026-09-25 각 2,698봉. 경계 사례 30개: 생존·분할·다중 클래스·ADR 정상, 상장폐지·합병(TWTR, SIVB, FRC, ATVI, XLNX, CELG, RTN, UTX, DWDP, WBA, X)은 폐지일까지. asof 동작: asof=2020-01-06 → FB 7봉·META 0봉 (36370815109). 무작위 상장폐지 150개: 아래 표본 결과 (36371216212) | 재사용 티커 섞임(아래). OTC 이후 가격 |
| Alpha Vantage LISTING_STATUS, 2016-01-04 active | **VERIFIED** (그 날짜) | HTTP 200 CSV 8,165행 (Stock 5,766 / ETF 2,399). 헤더 symbol, name, exchange, assetType, ipoDate, delistingDate, status (36370036672) | CIK 없음. 월중 변경은 알 수 없음 |
| Alpha Vantage LISTING_STATUS, 2016-01-04 delisted | **VERIFIED** (그 날짜) | 앞선 두 번의 `{}`(HTTP 200)는 연속 호출 한도 때문이었음. 15초 간격을 두자 CSV 946행 (Stock 789 / ETF 157, NASDAQ 471·NYSE 353·ARCA 121·BATS 1). 폐지일 1997-04-01~2015-12-31 — 그 날짜 이전에 폐지된 종목 (36372575004) | 그 날짜 이후의 폐지는 날짜 없는 전체 목록으로 |
| Alpha Vantage LISTING_STATUS, delisted 전체(날짜 없음) | **VERIFIED** (무료 키) | CSV 9,513행 (Stock 7,514 / ETF 1,999). 상장폐지일 1997-04-01~2026-09-25. 2016-02 이후 폐지된 Stock 6,714개 (36370276901, 36371216212) | CIK 없음. 폐지일이 일괄 날짜(2026-09-25, 2026-05-28)로 찍힌 행이 많아, 폐지일은 Polygon·SEC Form 25와 교차 확인해야 함. 합병 대가·OTC 이전 정보 없음 |
| Nasdaq Trader symbol directory | **VERIFIED** (현재 목록만) | nasdaqlisted 5,636행, otherlisted 7,652행. ETF 표시·종목명 포함 (36370036672). 앱의 증권 종류 판정에 사용 | 과거 이력은 아님. 매일 보존해야 함 |
| FINRA daily short-sale **volume** (CNMS) | **VERIFIED** (표본 날짜) | 2018-08-01 7,677행, 2018-08-02, 2019-01-02, 2024-01-02 (36370036672) | 공매도 **잔고**와는 다른 자료 |
| FINRA short **interest** API | **BLOCKED** | FINRA 키 없음 | 전부 |
| SEC companyfacts / submissions | **VERIFIED** | 2009년 이후 공시 fact와 accession·acceptance 시각 (probe 36324251338). 대형주 2,323개 중 1,954개 분기 해석 성공, 369개 실패 중 347개는 외국 발행사 (진단 36369154779) | 외국 발행사의 IFRS 분기 자료 |
| SEC 발행주식수 frames | **VERIFIED** | 대표 보통주 중 dei 4분기 78.2%, dei 8분기 + us-gaap CSO 83.0% (36370230494) | 약 17% |
| FRED/ALFRED | **VERIFIED** | VIXCLS 1990~, DGS10 1962~, DTB3 1954~, DEXKOUS 1981~, CPI 첫 빈티지 1972 (36324251338) | 발표 시각(시:분) |

## Alpaca — 상장폐지 표본과 재사용 티커 (run 36371216212)

**무작위 상장폐지 표본** (Alpha Vantage, 2016-02 이후 폐지된 Stock 150개, seed 20260928, 요청의 asof = 폐지일 전날)

| 결과 | 개수 |
|---|---|
| 일봉을 받음 | 116 / 150 |
| 못 받음 — 워런트·유닛·우선주 기호(TGNA-W, AJAX-WS, CYS-P-A …). Alpaca가 "invalid symbol"로 거절 | 30. 앱 유니버스(보통주) 밖 |
| 못 받음 — 거래소 상장 주식 | 4 (NXTV, CPTAF …) |
| 마지막 봉이 폐지일 7일 이내 | 91 / 116 |

- **거래소 주식만 보면** 116 / 120 = 96.7%를 받았습니다.
- **폐지일과 7일 넘게 차이 나는 25개:** 대부분 Alpha Vantage 폐지일이 의심스러운 경우입니다. 일괄 날짜로 찍혔거나, 거래소 퇴출 전에 거래가 먼저 멈춘 경우(OTC 이전)입니다.

**Tiingo:**
- 상장폐지 주식 가격은 Alpaca로 대부분 확보되므로, 지금은 필요하지 않다고 판단합니다.
- 수집기가 "시가총액 10억 달러 이상이었던 적이 있는 종목"을 정한 뒤, 그중 Alpaca가 못 주는 종목만 Tiingo 대상이 됩니다.
- 무료 한도(월 고유 종목 수·시간당 요청)는 이 환경에서 Tiingo 사이트가 막혀 확인하지 못했습니다. `TIINGO_API_KEY`로 실측하기 전까지는 UNVERIFIED입니다.

**재사용 티커**

2016년 이후 폐지되고 지금 다른 종목이 쓰는 티커가 400개입니다. 그중 60개를 확인했습니다.

| 결과 | 개수 | 예 |
|---|---|---|
| 기본 요청(asof 없음)에서 두 회사 가격이 **40일 넘는 공백을 사이에 두고 이어짐** | 8 | AAAP, AAC, APAC, APACU, APC, APXTU, ARIA, AT |
| 공백 없이 2016~2026이 한 줄로 이어지는데 옛 회사는 그 사이 폐지됨 → **공백 검사로는 못 잡는 섞임 가능** | 다수 | ADT, AMTD, B, BGC |
| asof를 폐지 전날로 주면 옛 회사 구간만 따로 받음 | 대부분 | ADT 1,090봉, AMTD 1,200봉 |
| asof 요청이 1봉 이하로 이상함 | 일부 | 우선주 ADAM*, AZ, BAM |

**수집기 방어 규칙**
1. 티커를 무기한(asof 없는) 요청으로 받지 않습니다. **상장 구간(시작일~폐지일)마다** 그 구간 안의 asof와 구간 범위로 요청합니다.
2. 구간 경계에서 가격이 급변하거나(종가 비율 ±50% 초과) 공백이 40일을 넘으면 그 구간은 **UNRESOLVED**로 두고 백테스트에서 뺍니다.
3. 상장 구간은 Alpha Vantage 폐지 목록, Polygon 참조(2년), SEC(Form 25, 공시 표지의 Trading Symbol)로 교차 확인합니다. 출처끼리 맞지 않으면 UNRESOLVED입니다.

## Alpaca 수집기 (2026-09-28 구현 — 실제 수집은 아직 안 함)

- **위치:** `backend/marketlens/backtest/alpaca.py`, 워크플로 `backtest-data` `task=alpaca`.
- **대상:** 끝난 Polygon 수집 DB를 이어받아 2016-01-01부터 Polygon 시작일 전날까지를 채웁니다.

**요청 방식**
- `asof=-`로 **그날 실제로 거래된 티커 그대로** 받습니다.
- 기본 요청은 META를 요청하면 FB 시절(2016~2022)까지 META 이름으로 돌려줍니다(probe run 36370815109: META 2,698봉, FB 0봉). 그러면 한 회사가 두 번 들어갑니다.
- 받은 가격은 Polygon 연도와 같은 “티커 → 회사(CIK) 구간표”로 회사에 연결합니다.

**방어 규칙 적용**
1. 가격은 구간표의 상장 구간별로 나눕니다. 어느 구간에도 속하지 않는 가격은 버리고 개수를 셉니다.
2. 아래 경우는 해당 구간을 **UNRESOLVED**(`bt_unresolved`)로 두고 백테스트에서 뺍니다. 가격은 감사용으로 남깁니다.
   - 구간 안에 40일 넘는 공백이 있을 때
   - 상장 경계 5거래일 안에서 분할로 설명되지 않는 ±50% 넘는 변동이 있을 때
   - 흔한 분할 비율(±3%)과 같은 변동인데 분할 기록이 없을 때
   - 분할 기록이 있는데 가격이 그 비율로 움직이지 않았을 때
   - 구간 한가운데의 큰 폭락은 **빼지 않습니다**. 빼면 생존 편향이 생깁니다.
3. Alpha Vantage 상장폐지 목록(무료 키 1회 호출)에 있는 폐지인데 구간표에 10일 안의 경계가 없으면 그 구간은 UNRESOLVED입니다. Alpha Vantage의 일괄 날짜(하루 50건 초과)는 쓰지 않습니다.

**그 밖의 처리**
- **Polygon 대조:** Polygon 기간의 첫 30일도 받아 같은 티커·같은 날 종가를 비교하고, 일치율을 기록합니다. 저장은 Polygon 시작일 전날까지만 합니다.
- **저유동 종목 제외:** 하루 최대 거래대금이 200만 달러에 못 미친 티커는 저장하지 않습니다(Polygon 연도와 같은 규칙).
- **함께 늘리는 자료**
  - Polygon 분할: 2015년부터 추가
  - 배당: 2016년부터 추가
  - SEC 공시: 이전 연도에 새로 거래대금 기준에 든 회사
  - FRED 주간 기록: 2016년부터
- **요청 한도:** Alpaca는 0.35초에 1번(분당 약 170회, 무료 한도 200회 미만)입니다. 429·5xx 응답이면 1분 이상 기다리고, 더 빨리 재시도하지 않습니다.
- **예상 시간:** 요청 한도로 계산한 값이며 실측 전입니다.
  - Alpaca 약 1,400회
  - Polygon 배당 수백 회(12.5초 간격)
  - FRED 약 10,800회(초당 2회)
  - 합계 약 3~4시간. 한 번에 320분을 넘으면 저장 후 이어받습니다.

## 다음 순서

1. Polygon 수집(run 36366933988)이 끝나면, 그 run을 `resume_run_id`로 넣어 `task=alpaca`를 실행합니다. 실행 결과의 커버리지, UNRESOLVED 수, Polygon 대조 일치율을 이 문서에 기록합니다.
2. **Alpha Vantage 무료 한도(일 25회) 배분:** 앱의 컨센서스 수집과 같은 키를 씁니다. 수집기는 목록 1회만 씁니다.
3. **백테스트 판정:** 2016년부터 데이터가 채워지면 판정을 다시 합니다. 그 전까지 판정은 “부족(예비)”입니다(`docs/backtest`).
4. **아직 하지 않은 교차 확인:** SEC Form 25·표지 Trading Symbol로 상장 구간을 맞춰 보는 일은 구현하지 않았습니다.
