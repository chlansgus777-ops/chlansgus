# 디자인 시스템

구현: `frontend/src/styles.css`(토큰·클래스), `frontend/src/components/ui.tsx`(컴포넌트). 방향: 국내 증권 앱처럼 깔끔하고,
SaaS 대시보드처럼 정돈된 **부드러운 다크 테마**.

## 색 토큰
| 토큰 | 값 | 용도 |
|---|---|---|
| `--bg` | #0f1216 | 앱 배경(순수 검정 아님) |
| `--surface` / `-2` / `-3` | #161a20 / #1c2129 / #242a33 | 카드 / 중첩 블록 / 입력·칩 |
| `--line` / `--line-strong` | #262c36 / #323a46 | 얇은 테두리 |
| `--text` / `--text-2` / `--muted` | #e8ebf0 / #b4bcc8 / #7d8795 | 본문 / 보조 / 캡션 |
| `--accent` | #5b8def | 주요 버튼·링크(강조색은 하나) |
| `--pos` | #2ec27e | 긍정·매수·상승 (▲, `+`와 함께) |
| `--neg` | #f0616d | 부정·손절·하락 (▼와 함께) |
| `--warn` | #f2b447 | 주의·쏠림·오래된 데이터 (`!`와 함께) |
| `--info` | #8f8cf6 | 참고 정보 |
| `--neutral` | #9aa4b2 | 관망·중립 |

각 의미색은 `-soft` 반투명 배경을 가진다(배지·알림 배경). 한국 시장 관행(상승=빨강)이 아니라 **미국 시장 관행(상승=초록)** 을 쓰며,
혼동을 막기 위해 항상 ▲/▼ 기호를 붙인다.

## 타이포그래피
글꼴: Pretendard → SUIT → 맑은 고딕 → Apple SD Gothic Neo → Noto Sans KR. 숫자는 `tnum`(고정폭 숫자). `word-break: keep-all`로 한국어 단어 단위 줄바꿈.
| 클래스 | 크기 | 용도 |
|---|---|---|
| `h1`/`.t-page` | 24px 700 | 화면 제목 |
| `h3`/`.t-section` | 17px 700 | 구역 제목 |
| `h2`/`.t-card` | 15px 650 | 카드 제목 |
| `.t-key` | 28px 750 | 핵심 숫자(가격·총액) |
| `.t-key-sm` | 20px 700 | 보조 핵심 숫자 |
| `.t-sub` / `.caption` | 14 / 12.5px | 설명 / 캡션 |

## 간격·모양
반경 14px(카드) / 9px(내부 블록), 간격 16px. 그림자 대신 얇은 테두리.

## 컴포넌트 (ui.tsx)
- `Card{title, icon, tone, explain, right}` — 모든 정보 묶음. `explain`은 제목 아래 한 줄 설명.
- `Action{action, expired, lg}` — 판정 배지(매수/소액 매수/추가매수/보유/관망/회피/매도). 만료 추천은 흐리게 + “만료”.
- `Quality`, `StatusBadge`, `Vetoes` — 데이터 품질·추천 유효 상태·거부권 사유(한국어).
- `Term{k}` — 용어 툴팁(glossary.ts).
- `Notice{tone}`, `Loading{what, steps}`, `Err{error, retry}`, `Empty{hint}` — 알림·상태 화면.
- `PriceLadder` — 손절·이상적 진입·최대 매수·목표를 한 막대에. 값이 같으면 한 라벨로 합치고, 가까우면 두 줄로 엇갈려 배치(`ladderMarks`, 테스트 있음).
- `PriceChart` — 종가 선 + 계획 가격 수평선. `Donut` — 비중. `Bar` — 점수 막대. `LineChart` — 자산 곡선.
- `EvidenceChips` — 근거 ID(자세히 보기 전용). `FreshnessTable` — 데이터 종류별 신선도.
- `mode.tsx`: `ModeProvider`, `ModeSwitch`(쉽게 보기/자세히 보기), `More`(쉽게 보기에서 접힘).

## 아이콘
유니코드 기호만 최소한으로(◎ ⌕ ◔ ∿ ▤ ◈ ↗ ● ⚙ ✎ ⚠). 아이콘만으로 의미를 전달하지 않고 항상 텍스트와 함께.
