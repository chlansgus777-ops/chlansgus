# MarketLens

**미국 주식 투자 의사결정 지원 시스템 (Windows 우선, 자동 주문 없음)**

MarketLens는 미국 상장(NASDAQ · NYSE · NYSE American) 전체 종목을 단계적으로 스캔하고, 가격·재무·실적/가이던스·
애널리스트 추정치 변화·밸류에이션·거시·뉴스/이슈·익스포저 그래프·수급/옵션·기술적 위치·포트폴리오 위험을
**결정론적으로** 계산한 뒤, 상위 후보에 한해 여러 전문 AI가 그 분석을 검증·반박하고, 어떤 종목을 **왜 / 어느
가격에 / 무엇이 잘못되면 버려야 하는지** 설명합니다. 실제 매매는 항상 사용자가 직접 합니다.

```
DATA FIRST · DETERMINISTIC CALCULATION FIRST · AI SECOND · EVIDENCE BEFORE OPINION
RISK BEFORE ACTION · POINT-IN-TIME CORRECTNESS · MEASURE EVERYTHING · CALIBRATE FROM REAL OUTCOMES
```

> ⚠️ 투자 권유가 아닌 의사결정 보조 도구입니다. MOCK 모드의 모든 숫자는 합성 데이터이며 화면에 **MOCK DATA** 로 표시됩니다.

---

## 빠른 시작

### 요구사항
- Python 3.11+
- Node.js 20+ (UI 빌드용)
- (선택) Windows 데스크톱 앱 빌드: Rust stable (MSVC) + WebView2 런타임

### Windows
```powershell
git clone <repo> MarketLens; cd MarketLens
powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1   # venv, 백엔드/프론트 설치, UI 빌드, DB 마이그레이션
run-windows.bat                                                       # 백엔드 실행 + 브라우저(http://127.0.0.1:8765) 열기
```
데스크톱 설치 파일(NSIS/MSI) 빌드: `scripts\windows\build.ps1` (PyInstaller 백엔드 사이드카 + Tauri 셸).
GitHub Actions `windows-build` 워크플로(수동 실행 또는 `v*` 태그)로도 빌드됩니다.

### macOS / Linux (개발)
```bash
python -m venv .venv && . .venv/bin/activate
pip install -e "./backend[dev,anthropic]"
cd frontend && npm ci && npm run build && cd ..
cp .env.example .env               # 기본 MARKETLENS_MODE=MOCK
python -m marketlens serve         # http://127.0.0.1:8765
```
UI 개발 서버: `cd frontend && npm run dev` (http://localhost:5173, `/api` 는 8765로 프록시).

### CLI
```bash
python -m marketlens scan [--no-committee]   # 전체 시장 스캔 (+ 상위 N 종목 AI 위원회)
python -m marketlens analyze NVDA [--committee]
python -m marketlens evaluate                # 추천 성과(1/5/20/60D) + Paper Trading 갱신
python -m marketlens calibrate               # 캘리브레이션 1회 (shadow / 승격 게이트)
python -m marketlens replay <rec_id>         # 저장된 스냅샷으로 과거 판단 재현
python -m marketlens simulate --weeks 12     # MOCK 전용: 과거 주간 스캔 재생 → 평가 대시보드 채우기
python -m marketlens migrate
```

---

## MOCK vs LIVE

| | MOCK (`MARKETLENS_MODE=MOCK`, 기본) | LIVE (`MARKETLENS_MODE=LIVE`) |
|---|---|---|
| 데이터 | 결정론적 합성 시장 600종목, 이름에 "(MOCK)", 모든 값 `mode=MOCK` | 설정된 실제 Provider만 |
| 화면 | 빨간 **MOCK DATA** 배너 | 초록 LIVE 배너 |
| 누락 데이터 | — | **MISSING / N/A 로 표시, 절대 Mock으로 대체하지 않음** |
| AI 위원회 | Mock LLM("(MOCK AI)") | Anthropic / OpenAI 호환 (키 필요). Mock LLM은 LIVE에서 거부 |
| DB | `data/marketlens_mock.db` | `data/marketlens.db` |

Mock과 Live Provider는 한 체인에 섞일 수 없습니다(`ModeMixError`).

## API 설정 (`.env`, `.env.example` 참조)

| 변수 | 용도 | 없을 때 |
|---|---|---|
| `SEC_USER_AGENT` | SEC EDGAR 유니버스 + XBRL 재무 (무료, 연락처 필수) | 유니버스/재무 MISSING |
| `FINNHUB_API_KEY` | 실시간 시세, 기업 뉴스, 실적 일정, EPS 실적 vs 추정 이력 | 시세/뉴스/일정 MISSING |
| `POLYGON_API_KEY` | 일봉 (grouped daily로 전체 시장) | 가격 이력 MISSING |
| `FRED_API_KEY` | 금리·물가·고용·GDP·달러·유가·VIX·HY 스프레드·지수 | 거시 MISSING |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` + `LLM_PROVIDER` | AI 위원회 | **AI COMMITTEE UNAVAILABLE** (결정론 기능은 정상) |

키는 코드에 하드코딩하지 않습니다. `.env` 또는 OS 키체인(`pip install marketlens[keyring]`, 서비스명 `marketlens`)을 사용하며 로그에서 자동 마스킹됩니다.

## 지원 데이터 / 미지원 데이터 (LIVE 기준, 자세히: `docs/DATA_SOURCES.md`)
- **지원**: 미국 상장 유니버스(SEC), 분기 재무(SEC XBRL, point-in-time filed_date), 실시간 시세(Finnhub), 일봉(Polygon),
  뉴스(Finnhub), 실적 일정·EPS 서프라이즈 이력(Finnhub), 거시(FRED), 모든 파생 계산(기술지표, 밸류에이션, R/R 등).
- **라이선스 필요 → 현재 MISSING**: EPS/매출 추정치 리비전(7/30/90D), 매출 컨센서스·가이던스, 과거 밸류에이션 시계열,
  옵션(IV/기대변동폭/OI), 공매도, 내부자, 기관 보유, 섹터 KPI(CET1, NIM, RPO, 점유율, FFO 등), 유니버스의 섹터/시가총액,
  SOX·NDX·RUT·금·시장 폭, FOMC/CPI 등 거시 일정.
- ADR/외국 발행사(IFRS·20-F)는 SEC 분기 데이터가 부족할 수 있어 `FundamentalProvider` 추상화로 보조 공급자 연결이 필요합니다.

## 동작 방식

### 전체시장 스캐너 (cheap → expensive)
1. **Eligibility** — 가격 ≥ $5, 시총 ≥ $1B, 20일 평균 거래대금 ≥ $20M, 상장 상태 (config 변경 가능)
2. **Cheap quant** — 매출/EPS 성장, EPS 리비전, 상대강도, 거래량 추세, 52주 고점 거리, Forward PE, FCF yield, 유동성, 변동성의 횡단면 퍼센타일 (펀더멘털 우선, 기술적 요소는 소비중) → ~400
3. **Fundamental deep** — 섹터 모델 + 밸류에이션 + 실적/리비전 → ~150
4. **Event / Issue** — 뉴스 → 이슈 → 익스포저 그래프(최대 2-hop) → 시간축별 영향 → Priced-in, 옵션·수급 → ~60
5. **Final ranking** — 전체 결정론 분석 + 결정 → ~40, 그중 **상위 `AI_COMMITTEE_TOP_N`(20)만 AI 위원회**

### 결정론적 점수 (0–100, `docs/SCORING_MODEL.md`)
Fundamental 25 · Valuation 15 · Earnings & Revision 15 · Catalyst 10 · Macro 10 · Technical 10 · Risk 10 · Entry R/R 15
— 모든 점수는 근거(evidence ID)와 함께 분해되어 표시됩니다. NVDA(반도체)와 JPM(은행)은 서로 다른 섹터 모델로 평가됩니다.

### 최종 결정
BUY / BUY SMALL / ADD / HOLD / WATCH / WAIT / REDUCE / SELL / DATA INSUFFICIENT
- **Hard Veto**: STALE_PRICE, MISSING_CORE_DATA, SEVERE_DATA_CONFLICT, THESIS_INVALIDATED, UNACCEPTABLE_LIQUIDITY, EXTREME_EVENT_RISK
- **Hysteresis**: BUY 진입 ≥ 80 / 이탈 < 76 · BUY SMALL 72 / 68
- **Material Change**: 실적·가이던스·리비전·주요 이슈·레짐 변화·매수구간 진입/이탈·논리 훼손·R/R 변화가 없으면 추천을 바꾸지 않음
- **Entry Engine**: 이상적 진입가, 허용 범위, **최소 R/R(2.0)에서 역산한 최대 매수가**, 추가매수 구간, 손절, 목표 1/2
- **Thesis invalidation**은 가격 손절과 별도로 관리 (`config/theses.toml`)

### AI Investment Committee (`docs/AI_COMMITTEE.md`)
Fundamental · Earnings · Valuation · Macro · Technical · News & Issue · Risk 분석가 → Bull/Bear 토론 2라운드(증거 인용 필수) →
Decision Synthesizer → Risk Manager(**하향만 가능**) → Portfolio Manager(결정론적 한도 이하 사이즈). 엄격한 스키마, 증거 ID 검증,
**증거에 없는 숫자 제거**, 프롬프트 인젝션 방어, 가중 합의도(Committee Consensus %)와 의견 분산(HIGH DIVERGENCE → 신뢰도 하향).
AI는 가격·데이터·결정론 점수를 바꿀 수 없고 Hard Veto를 무시할 수 없습니다. 같은 스냅샷은 캐시되며 토큰/비용을 기록합니다.

### Paper Trading (`docs/PAPER_TRADING.md`)
BUY/BUY SMALL/ADD 추천 → 추천 시각 **이후 첫 거래 가능한 시가** + 슬리피지 + 스프레드로 가상 진입. 손절(갭 반영)·목표1(50%)·목표2·
기간 청산·논리 훼손·추천 하향으로 청산. 승률, 평균/중앙 수익, Profit Factor, 기대값, 최대낙폭, 보유기간, MAE/MFE, SPY 대비 초과수익.

### 성과 평가 & Calibration (`docs/MODEL_EVALUATION.md`, `docs/CALIBRATION.md`)
추천 후 1/5/20/60 **거래일** 수익률(기간이 실제로 지난 뒤에만 기록), 요인별 Spearman IC·IR(표본 부족 시 N/A).
가중치는 최소 100 표본, 사이클당 ±5%(상대), shadow 모드에서 표본 외 IC 개선 + 적중률·하방위험 악화 없음일 때만 승격.

### 재현성 / 감사
모든 추천에 point-in-time 입력 스냅샷, 설정 TOML 원문, 가중치, 입력 지문, scoring/decision/prompt/provider/config/schema 버전을 저장.
`replay`는 현재 데이터를 다시 가져오지 않고 스냅샷만으로 재계산해 일치 여부를 확인합니다.

## 화면
Dashboard(가장 먼저 "지금 가장 매력적인 종목") · Opportunities · Market · Stocks(최종 추천 화면: Action/Score/Confidence/현재가·세션·시각·출처,
Price Plan, Why, 이슈 타임라인 Today/1–5D/2–6W/1–4Q, Thesis Invalidation, What Changed, Score Breakdown, 재무·실적·리비전·밸류에이션·거시·기술·이슈·Priced-in·
카탈리스트·옵션·리스크, 시나리오, AI 위원회, 근거 소스) · Watchlist · Portfolio · Issues · Macro · Calendar · AI Committee(View Full Debate) ·
Model Performance · System Health · Settings. 시간은 사용자 현지 시각 + ET 병기.

## 프로젝트 구조
```
backend/marketlens/  domain/ (순수 계산) · providers/ (계약·Mock·Live·LLM·라우터) · infrastructure/ (DB·회복탄력성·헬스·로깅)
                     application/ (파이프라인·스캐너·이슈·위원회·서비스·평가·재현) · api/ (FastAPI) · workers/ (CLI·스케줄러)
backend/alembic/     DB 마이그레이션          backend/tests/  단위·계약·아키텍처·AI안전·골든·회귀·룩어헤드·통합
frontend/            React + TypeScript(strict) + Vite, src-tauri/ (Tauri 2 데스크톱 셸)
config/              scoring_model.toml · sector_models.toml · exposure_graph.toml · theses.toml (모두 버전 관리)
docs/                ARCHITECTURE · DATA_SOURCES · SCORING_MODEL · SECTOR_MODELS · ISSUE_IMPACT_MODEL · AI_COMMITTEE · PAPER_TRADING ·
                     CALIBRATION · MODEL_EVALUATION · PROVIDER_HEALTH · SECURITY · PANWATCH_ANALYSIS · adr/
```

## 테스트
```bash
cd backend && python -m pytest            # 백엔드 (unit / contract / architecture / ai_safety / golden / regression / backtest / integration)
cd frontend && npm run typecheck && npm test && npm run build
```
의도한 모델 변경 시: `config/scoring_model.toml`의 `scoring_model_version`을 올리고 `UPDATE_BASELINE=1 python -m pytest tests/regression`.

## Limitations (현재 한계)
- LIVE에서 추정치 리비전·컨센서스·가이던스·옵션·공매도·내부자·기관·섹터 KPI·밸류에이션 이력은 라이선스 공급자 연결 전까지 MISSING →
  해당 점수 요소가 보수적(0.35)으로 처리되어 LIVE 점수는 MOCK보다 낮게/불완전하게 나올 수 있습니다.
- SEC 유니버스 파일에는 섹터/시총이 없어 LIVE Stage 1은 시총을 제공하는 공급자 연결이 필요합니다(없으면 제외됨).
- 무료 Polygon 요율로는 수천 종목 일봉 초기 적재에 시간이 걸립니다(grouped daily 일일 동기화 권장).
- 이슈 구조화는 규칙 기반(키워드 분류)이며, 익스포저 그래프는 큐레이션된 소수 관계 + 섹터 기본 민감도입니다.
- Priced-in·시나리오는 추정치이며 시나리오 확률은 보정 전까지 N/A입니다.
- Tauri 설치 파일은 Windows 빌드 환경(Actions `windows-latest` 또는 로컬 Windows)에서만 생성됩니다.
- 투자 판단의 최종 책임은 사용자에게 있습니다.

## PanWatch
첨부된 PanWatch(MIT)는 소스 수준에서 분석했지만 코드는 복사하지 않았고 런타임 의존성도 없습니다. 흡수한 장점/버린 단점: `docs/PANWATCH_ANALYSIS.md`.
