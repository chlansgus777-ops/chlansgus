# MarketLens UI 개편 보고 (MarketHUD 참고) — 2026-09-28

- **작업 방식:** 별도 로컬 브랜치 `ui/markethud-overhaul`에서 작업한 뒤 `claude/marketlens-investment-system-iyki44`에 합쳤습니다.
- **기술 스택:** 그대로입니다(React 19 + TS + Vite + react-router 7 + vitest, Tauri).
- **새 런타임 의존성:** 없습니다. 글꼴 파일 1개만 추가했습니다.
- **캡처 조건:** 모든 캡처는 이 개발 환경의 Chromium(Playwright)에서 **MOCK 모드 백엔드**로 실행한 화면입니다. 실데이터(LIVE) 화면이 아닙니다.

## 1. 채택한 MarketHUD 요소

| MarketHUD (v5.28) | MarketLens 적용 | 바꾼 점 |
|---|---|---|
| 짙은 남색 배경 + 보라 강조색 팔레트(`style.py`) | `styles.css` 토큰(`--bg #080b13`, `--accent #9e8af3`, `--lilac`) | 캡션 색을 #747a8e(4.3:1)에서 #979db1(6.8:1)로, 버튼색을 #7668e8(4.3:1)에서 #6a5bdc(5.1:1)로 바꿔 대비를 올림. 그라데이션·발광 제거 |
| commandCard: 큰 행동 문구 + 왼쪽 색 띠 | 종목 ① 결론 카드(`.command`, 판단 종류별 띠 색) | 만료·조건 이탈이면 점선 띠 + “지금은 유효하지 않은 매수 신호” |
| 2×2 commandMetric 타일 | 결론 카드 타일: 현재가(기준 시각) · 최대 매수가 · 손절(종가) · 가장 큰 위험 | 작은 창에서도 결론 바로 아래에 보임 |
| SafetyAlertRibbon | `Ribbon` — MOCK·준비도·새로고침 실패 등 데이터 권한 문제에만 사용 | — |
| DisclosureSection ▸/▾ | `Disclosure`/`More` — 상세 자료, AI 토론 전체 | 쉽게 보기에서는 접힘 |
| Pill, tier 번호 | `Pill`, `Section`(①~⑧ 번호 제목) | — |
| 차트 | 결정 정보 옆 배치 | ③ 가격 계획을 “표 \| 차트” 2열로. 차트 계획선은 백엔드 값 |
| MetricCard ▲/▼ mint/coral | `Change` — 가격 변화에만 사용 | 매수·매도 판단 색(파랑·주황)과 분리 |
| Pretendard 우선 글꼴 | Pretendard Variable을 앱에 포함해 로컬 로드 | 숫자는 tabular-nums(설치 여부와 무관) |

**채택하지 않은 것:** MarketHUD의 계산·표시 조건·공급자·계좌 연결은 가져오지 않았습니다. 미니 창도 적용하지 않았습니다(6절).

## 2. 개선된 사용자 흐름

**첫 화면(홈)**
- 순서: ① 오늘 시장 분위기 → ② 검토 후보 수 → ③ 가장 큰 위험 → ④ 다음 핵심 일정 → 후보 카드.
- **후보 카드는 최대 5개입니다.** 매수 계열이면서 지금 다시 판정해도 유효하고, 가격·데이터가 오래되지 않은 종목만 보여줍니다.
  - 적으면 “빈자리를 점수 상위 종목으로 채우지 않았습니다”라고 밝힙니다.
  - 만료·재확인 필요·가격 조건 이탈 추천은 카드가 아닌 ‘지금은 유효하지 않은 추천’ 목록에 둡니다.
- **카드 내용**
  - 판단과 쉬운 설명
  - 핵심 이유 1줄: 백엔드에 저장된 분석 문장
  - 현재가와 그 가격의 기준 시각
  - 검토 가격대: 이상적 진입가 ~ 최대 매수가
  - 손절 기준
  - 가장 큰 위험: 저장된 문장
  - “점수·신뢰도·손익비는 상승 확률이 아닙니다”
- **스캔 범위:** “모의 종목 목록 594개 → 기준 통과 388 → 정밀 분석 60 → 최종 40”처럼 실제 범위만 적습니다. 버튼 이름은 ‘전체 시장 스캔’에서 ‘시장 스캔 실행’으로 바꿨습니다.

**상단 상태 표시줄(모든 화면)**
- MOCK/LIVE
- 미국장 세션: 백엔드 거래소 달력 기준(휴장일 포함)
- 뉴욕 시각과 한국 시각
- 이 화면 가격의 기준 시각과 경과 시간. 화면을 불러온 시각은 가격 시각으로 쓰지 않습니다.
- 분석 시각
- 데이터 수집 %, 스캔 진행
- 공급자 중단 수, AI 사용 가능 여부

**종목 상세 순서**
1. 결론
2. 판단 이유: 결정론적 근거, 그다음 AI 요약(긍정 근거·반대 근거·가장 큰 불확실성·종합). 토론 전체는 접어 둡니다.
3. 가격 계획
   - 이상적 진입가, 허용 구간, 최대 매수가
   - 추가 매수 조건, 손절(종가), 1·2차 목표, 손익비
   - 현재 위치
   - 값이 없으면 “자료 부족”
4. 위험
5. 판단 철회 조건: “가격 기준 — 손절”과 “투자 논리 기준 — 가격과 별개”로 나눔
6. 뉴스·이슈·일정: 시스템 해석이라고 명시
7. 내 포트폴리오와의 관계
8. 상세 자료(접힘)

**상태 10종 (`StatePanel`)**
- 수집 중 / 분석 중 / 스캔 안 함 / 후보 없음 / 자료 부족 / 오래됨 / 가격 조건 이탈 / 공급자 오류 / AI 불가 / 연결 끊김
- 각각 “무슨 상황 / 알 수 없는 것 / 할 수 있는 일”을 보여줍니다.
- 새로고침에 실패하면 이전 결과를 **받은 시각과 함께** 유지합니다(`StaleData`).

**용어 설명**
- `Term`은 버튼입니다. 클릭, Enter·Space, 터치로 열고 Esc로 닫습니다.
- 손익비 설명: “손익비 2:1 → 계획상 손실 위험 1에 비해 목표 이익이 2라는 뜻입니다. 목표 달성 확률을 의미하지 않습니다.”
- IV, 판단 철회 조건 설명을 추가했습니다.

**포트폴리오·성과·이슈·AI 위원회**
- **포트폴리오:** 가격이 없는 보유 종목은 평가·비중·손익에서 뺀다고 밝힙니다(매입가로 대체하지 않음, 백엔드 동작 그대로).
- **성과:** MOCK 성과는 “실전 성과가 아님” 리본을 답니다. LIVE는 “실데이터 기반 모의투자”로 표시합니다. 기간, 기준일, 전체/독립 표본, 결과 확정/대기 표본, 평가 못 한 신호, 벤치마크 SPY를 보여줍니다.
- **이슈:** 표 머리를 “기사·출처에 나온 사실”과 “MarketLens 해석”으로 나눴습니다.
- **AI 위원회 화면:** 종목을 바꾼 뒤 늦게 도착한 이전 종목의 실행 결과는 버립니다. **기존에는 표시될 수 있던 결함을 이번에 고쳤습니다.**

## 3. 변경 파일

**백엔드: 읽기 전용 표시 필드만 추가했습니다.** 기존 필드·점수·추천·거부권·신선도·버전 규칙은 바꾸지 않았습니다.
- `backend/marketlens/api/routes.py`
  - `/api/system`에 `market.session`, `ny_time`, `last_completed_session` 추가. 기존 거래소 달력 `classify_session`을 씁니다.
  - `/api/dashboard` 후보 행에 `key_reason`, `key_risk` 추가. 저장된 분석에서 문장을 **골라 읽기만** 하며, 새 분석이나 시세 조회는 하지 않습니다.
  - 요청하신 “API 계약 변경 금지”에 비추어 알려드립니다: 기존 응답은 그대로이고 필드만 추가했습니다. 첫 화면이 세션을 PC 시계로 추정하거나 이유 문장을 화면에서 만들어 내지 않게 하려는 것입니다.
- `backend/tests/integration/test_ui_display_fields.py` (새 파일, 3개): 휴장일(추수감사절)=CLOSED, 08:00 ET=PREMARKET, 카드 문장이 저장된 문장 중 하나인지, 거부권→이벤트→부정 이유 순서.
- `backend/tests/e2e/test_ui_e2e.py`: **제가 이전 회차에 만든 테스트의 제목 문자열 2개만 바꿨습니다.** ‘지금 가장 유망한 종목’→‘지금 검토할 후보’, ‘매수 계획’→‘가격 계획’. 단언 개수와 조건은 그대로입니다.

**프론트엔드**
- **새 파일**
  - `src/components/status.tsx`: 상태 표시줄. 가벼운 상태 조회만 하고, 실행 중일 때만 짧게 폴링합니다.
  - `src/assets/fonts/PretendardVariable.woff2` + `Pretendard-LICENSE.txt`, `public/licenses/Pretendard-OFL-1.1.txt`
  - `src/pages/ui_overhaul.test.tsx` (23개), `src/design.test.ts` (3개), `src/__fixtures__/stock_mock.json`: MOCK 백엔드 실제 응답을 줄인 것
- **다시 작성:** `styles.css`, `components/ui.tsx`, `App.tsx`, `pages/Dashboard.tsx`, `pages/StockDetail.tsx`(내보내는 함수와 동작은 유지), `pages/Committee.tsx`
- **수정:** `components/CommitteeView.tsx`(`CommitteeSummary` 추가), `Readiness.tsx`, `useApi.ts`(`fetchedAt`, `usePoll`), `format.ts`, `glossary.ts`, `i18n.ts`, `mode.tsx`, `types.ts`, `pages/{Portfolio,Performance,Opportunities,Health,Issues,Macro,Guide}.tsx`
- **문서:** `docs/DESIGN_SYSTEM.md`, 이 보고서, `docs/ui/screens/*.png`

## 4. 개편 전후 화면 (`docs/ui/screens/`)

**창 크기 3종**
- 작은 창: 800×600
- 보통: 1440×900
- 고배율: 1280×720 @ 150% = 물리 1920×1080, Windows 150% 배율과 같은 조건

| 화면 | 개편 전 | 개편 후 |
|---|---|---|
| 홈 (보통) | before-dashboard-normal.png | after-dashboard-normal.png |
| 홈 (작은 창) | before-dashboard-small.png | after-dashboard-small.png |
| 종목 (보통) | before-stock-normal.png | after-stock-normal.png |
| 종목 (작은 창) | before-stock-small.png | after-stock-small.png |
| 종목 (150%) | before-stock-hidpi150.png | after-stock-hidpi150.png |
| 포트폴리오 | before-portfolio-normal.png | after-portfolio-normal.png |
| 성과 | before-performance-normal.png | after-performance-normal.png |

**상태 화면 (개편 후)**
- 응답을 브라우저에서 가로채 상태를 강제로 만들었습니다.
- 파일: `after-state-{stale, out_of_range, insufficient, ai_unavailable, no_candidates, provider_failure, collecting, analyzing, disconnected}.png`

## 5. 실행한 검사와 결과

| 검사 | 결과 |
|---|---|
| typecheck `tsc -b` | 통과 |
| vitest 전체 | **91개 통과**: 기존 65개는 수정 없이 통과, 새로 26개 |
| 새 테스트 자체 점검 | 후보 필터와 ‘자료 부족’ 표시를 일부러 망가뜨리자 새 테스트 3개가 실패함. 원상 복구 |
| production build `vite build` | 통과 (JS 452KB, CSS 24KB, 글꼴 2.06MB) |
| 백엔드 전체 pytest | 통과(종료 코드 0), 새 표시 필드 테스트 3개 포함 |
| 브라우저 E2E `tests/e2e/test_ui_e2e.py` | 14개 통과. 빌드한 UI를 백엔드가 제공, Chromium 사용 |
| 창 크기 (800×600 / 1440×900 / 1280×720@1.5) | 전 화면 캡처 |
| 760px 가로 스크롤 (12개 화면) | 처음에 ‘시장·거시 지표’ 화면이 883px로 넘침 → 표를 스크롤 상자로 감싸 수정 → 12개 모두 없음 |
| 키보드 포커스 | Tab 40회: 메뉴 → 상태 표시줄 → 결론 → 용어 → 버튼 → 펼침 순서. 모든 포커스에 윤곽선 표시 |
| 용어 설명 키보드 | Enter로 열림, Esc로 닫힘 |
| 대비 (WCAG 계산) | 토큰 전경/배경 모두 6.0:1 이상. 배지 5.9:1 이상. 주요 버튼 5.1:1 |
| 빠른 종목 전환 | 이전 종목의 늦은 응답이 화면에 안 나옴: 단위 테스트 + 기존 E2E |
| 추천·AI 버전 일치 | 다른 추천 id의 위원회 결과는 표시 안 함(단위 테스트). 위원회 화면의 늦은 실행 결과 버림(단위 테스트) |
| 오래됨·자료 부족·공급자 오류 상태 | 단위 테스트 + 브라우저 캡처 9종 |
| MOCK/LIVE 구분 | 상태 표시줄·리본·성과 화면 단위 테스트 + 캡처 |
| 중복 분석·AI 호출 | 브라우저에서 홈↔종목↔포트폴리오를 6회 이동: 요청은 저장 결과 읽기(GET)뿐. POST·`refresh=true`·`/committee` 0건. 단위 테스트도 같은 조건 확인 |
| 글꼴 | 빌드본을 백엔드로 열었을 때 `Pretendard Variable` loaded. 글꼴 요청은 앱 자체 파일 1건(`font/woff2`), 외부 요청 없음 |
| 숫자 폭 (tabular-nums) | `$111.11`과 `$888.88` 폭이 모두 85.39px로 같음 (비례 숫자라면 63.97px / 83.11px) |

## 6. 확인하지 못한 환경과 남은 한계

**확인하지 못한 환경**
- **Windows 실행·설치본(Tauri NSIS/MSI)은 빌드·실행하지 않았습니다.** 창 크기와 150% 배율은 Linux Chromium에서 같은 CSS 픽셀 조건으로 흉내 냈을 뿐입니다. WebView2의 실제 글꼴 렌더링, 배율 전환, 창 최소 크기는 새 Windows 빌드에서 확인해야 합니다.
- **LIVE 모드 화면은 실제 키로 보지 않았습니다.** 공급자 오류·수집 중 화면은 MOCK 응답을 가로채 만든 것입니다.
- 스크린리더(NVDA 등)와 터치 기기 실물로는 시험하지 않았습니다. 대신 역할·aria-expanded·키보드 동작을 확인했습니다.

**미니 창(항상 위)**
- 적용하지 않았습니다.
- 현재 구조에서는 창을 Rust(`src-tauri`)가 만들고 API 토큰을 주입하며, 권한(capabilities)은 `main` 창에만 있습니다.
- 두 번째 창을 만들려면 새 IPC 명령·권한과 토큰 주입이 필요합니다. 여기서는 Windows에서 ‘항상 위’가 실제로 동작하는지 확인할 수 없습니다.

**남은 한계**
- **글꼴 용량:** 약 2MB가 설치본 크기에 더해집니다. 부분 글꼴(subset)로 줄일 수 있지만, 모든 한글이 들어 있는 전체 파일을 택했습니다.
- **가격 기준 시각:** 상태 표시줄의 “후보 가격 기준”은 보이는 후보 카드 중 가장 최근 가격 시각입니다. 카드마다 다를 수 있어 각 카드에 자기 시각을 따로 적었습니다.
- **세션 갱신 간격:** 장 세션은 1분마다 `/system`을 다시 읽어 바뀝니다. 장 시작 직후 최대 1분은 늦게 바뀔 수 있습니다.
- **탭 이동 시 요청:** 화면 이동마다 저장된 결과를 다시 읽습니다(GET). 분석·AI 호출은 아니며, 시세는 서버 캐시를 먼저 씁니다.
