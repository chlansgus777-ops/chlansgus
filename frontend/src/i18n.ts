/** Korean labels and precise explanations. Terms keep their original English as well (e.g. "BUY SMALL(소량 매수)")
 * so the meaning is never simplified away. */

export interface Info { label: string; help: string }

export const ACTION_INFO: Record<string, Info> = {
  "BUY": { label: "매수", help: "점수가 매수 기준(80점) 이상이고, 현재가가 최대 매수가 이하이며, 손익비(목표가까지 상승폭 ÷ 손절가까지 하락폭)가 2 이상이고, 데이터·유동성·이벤트·포트폴리오 한도를 모두 통과한 상태입니다. 주문은 직접 판단해 실행하세요." },
  "BUY SMALL": { label: "소량 매수", help: "매수 조건은 대체로 충족하지만 점수(72~80점) 또는 위험(극단적 이벤트, 포트폴리오 한도, 업종 분류 불명확 등) 때문에 평소보다 작은 비중만 허용됩니다." },
  "ADD": { label: "추가 매수", help: "이미 보유 중인 종목에서 추가매수 구간에 있고 손익비 2 이상이며, 추가 후 종목·섹터 비중이 한도를 넘지 않을 때만 표시됩니다." },
  "HOLD": { label: "보유 유지", help: "보유 중이며 매도·축소 사유는 없지만 추가매수 조건도 충족하지 않습니다." },
  "WATCH": { label: "관찰", help: "현재 매수 매력이 부족하거나(점수 미달) 포트폴리오 한도 때문에 관찰만 권고합니다." },
  "WAIT": { label: "대기", help: "종목 자체 점수는 괜찮지만 가격이 최대 매수가를 넘었거나, 손익비가 부족하거나, 이벤트·논리 훼손 때문에 지금은 진입하지 않습니다." },
  "REDUCE": { label: "비중 축소", help: "보유 중이며 점수가 보유 기준(55점) 아래로 내려갔습니다." },
  "SELL": { label: "매도", help: "보유 중이며 점수가 45점 미만이거나, 투자 논리가 훼손되었거나, 직전 추천의 손절가를 이탈했습니다." },
  "DATA INSUFFICIENT": { label: "데이터 부족", help: "핵심 데이터(현재가·가격 이력·재무제표)가 없거나 오래되었거나 서로 충돌해 어떤 행동도 권고하지 않습니다. 점수가 높아도 이 판정은 AI가 바꿀 수 없습니다." },
};

export const BULLISH = new Set(["BUY", "BUY SMALL", "ADD"]);

export const QUALITY_INFO: Record<string, Info> = {
  FRESH: { label: "최신", help: "데이터 종류별 기준 이내로 최신입니다(예: 일봉은 최근 1거래일, 분기 재무는 결산 후 190일 이내)." },
  DELAYED: { label: "지연", help: "최신은 아니지만 사용 가능한 범위입니다(예: 실시간이 아닌 지연 시세). 신뢰도가 낮아집니다." },
  STALE: { label: "오래됨", help: "허용 한도를 넘어 오래된 데이터입니다. 핵심 데이터가 오래되면 판단하지 않습니다(데이터 부족)." },
  MISSING: { label: "없음", help: "데이터를 받지 못했거나 계산할 만큼 충분하지 않습니다. 다른 값으로 대체하지 않습니다." },
  CONFLICTING: { label: "충돌", help: "공급자 간 값이 크게 다르거나, 분석 시점 이후 날짜의 데이터입니다. 사용하지 않습니다." },
  PARTIAL: { label: "일부 누락", help: "핵심 데이터는 있지만 보조 데이터 일부가 없거나 오래되었습니다. 해당 항목은 보수적으로 처리됩니다." },
};

export const STATUS_INFO: Record<string, Info> = {
  CURRENT: { label: "현재 유효", help: "분석한 지 얼마 안 됐거나, 현재가로 최대 매수가·손절가·손익비를 다시 확인해 통과했습니다." },
  NEEDS_REVALIDATION: { label: "현재가 재확인 필요", help: "장중에 분석한 지 시간이 지나 가격이 바뀌었을 수 있습니다. 현재가로 다시 확인하기 전에는 실행하지 마세요." },
  PLAN_INVALIDATED: { label: "가격 조건 이탈", help: "현재가가 최대 매수가를 넘었거나 손절 기준 아래이거나 손익비가 부족해 이 매수 계획은 지금 유효하지 않습니다." },
  AGING: { label: "재분석 필요", help: "추천 이후 거래일이 지나 가격이 달라졌을 수 있습니다. 가격 계획은 참고만 하고 재분석하세요." },
  EXPIRED: { label: "만료", help: "추천 이후 여러 거래일이 지났거나 추천 당시에도 데이터가 부족했습니다. 현재 판단 근거로 사용하지 마세요." },
};

export const COMPONENT_KO: Record<string, string> = {
  fundamental: "펀더멘털", valuation: "밸류에이션", earnings_revision: "실적·추정치 리비전", catalyst: "촉매·이슈",
  macro: "거시", technical: "기술적 흐름", risk: "위험", entry_rr: "진입 손익비",
};

export const HORIZON_KO: [string, string][] = [["IMMEDIATE", "당일"], ["SHORT", "1~5일"], ["SWING", "2~6주"], ["FUNDAMENTAL", "1~4분기"]];

export const DATA_TYPE_KO: Record<string, string> = {
  price: "현재가", price_history: "일봉 가격 이력", fundamentals: "재무제표(분기)", earnings: "실적 발표", analyst: "애널리스트 추정치",
  macro: "거시 지표", news: "뉴스", options: "옵션", short_interest: "공매도 잔고", sector: "업종 분류",
};

export const VETO_KO: Record<string, string> = {
  STALE_PRICE: "현재가 오래됨/없음", STALE_CORE_DATA: "핵심 데이터 오래됨", MISSING_CORE_DATA: "핵심 데이터 부족", SEVERE_DATA_CONFLICT: "데이터 충돌",
  THESIS_INVALIDATED: "투자 논리 훼손", UNACCEPTABLE_LIQUIDITY: "유동성 부족", EXTREME_EVENT_RISK: "극단적 이벤트 위험",
  INSUFFICIENT_MODEL_COVERAGE: "업종 모델 판단 데이터 부족",
  SEVERE_ESTIMATE_CONFLICT: "추정치 공급자 간 심각한 불일치 — 신규 매수 보류",
};

export const SESSION_KO: Record<string, string> = { PREMARKET: "프리마켓", REGULAR: "정규장", AFTER_HOURS: "애프터마켓", CLOSED: "장 마감", OVERNIGHT: "야간" };
export const RISK_KO: Record<string, string> = { LOW: "낮음", MEDIUM: "보통", HIGH: "높음", EXTREME: "극단적" };
export const STANCE_KO: Record<string, string> = { positive: "긍정", neutral: "중립", negative: "부정" };
export const SIZE_KO: Record<string, string> = { FULL: "정상 비중", HALF: "절반 비중", SMALL: "소량", WATCH: "편입 불가(관찰)" };
export const REGIME_KO: Record<string, string> = {
  "Risk On": "위험 선호(Risk On)", "Risk Off": "위험 회피(Risk Off)", "Inflation Shock": "인플레이션 충격(Inflation Shock)",
  "Growth Scare": "성장 둔화 우려(Growth Scare)", "Liquidity Expansion": "유동성 확장(Liquidity Expansion)",
  "Liquidity Tightening": "유동성 긴축(Liquidity Tightening)", "AI Momentum": "AI 모멘텀(AI Momentum)",
  "Defensive Rotation": "방어주 순환(Defensive Rotation)", "Commodity Shock": "원자재 충격(Commodity Shock)",
  "Credit Stress": "신용 경색(Credit Stress)", "Multiple Expansion": "밸류에이션 배수 확장(Multiple Expansion)",
  "Multiple Compression": "밸류에이션 배수 축소(Multiple Compression)", Neutral: "중립(뚜렷한 국면 없음)", Unknown: "판단 불가(거시 데이터 없음)",
};

export function actionLabel(a: string | null | undefined): string {
  if (!a) return "없음";
  const i = ACTION_INFO[a];
  return i ? `${i.label}(${a})` : a;
}

export function ko(map: Record<string, string>, key: string | null | undefined, fallback = "없음"): string {
  if (!key) return fallback;
  return map[key] ?? key;
}

/** Recommendation readiness gate (backend: application/readiness.py). */
export const READINESS_KO: Record<string, { label: string; tone: string; help: string }> = {
  FULL: { label: "실전 참고 가능", tone: "pos", help: "모든 판단 재료가 실데이터로 연결되어 있습니다." },
  LIMITED: { label: "제한적 참고", tone: "warn", help: "핵심 데이터는 준비됐지만 일부 재료(옵션·기관 보유·추정치 이력 등)가 없거나 부분적입니다. 추천을 그대로 따르지 말고 참고용으로 쓰세요." },
  "PAPER ONLY": { label: "연습용(모의)", tone: "info", help: "모의 데이터입니다. 실제 투자 판단에 쓰면 안 됩니다." },
  "NOT READY": { label: "준비 안 됨", tone: "neg", help: "데이터 준비가 끝나지 않았습니다. 지금의 추천(또는 빈 목록)은 판단 근거가 될 수 없습니다." },
};
