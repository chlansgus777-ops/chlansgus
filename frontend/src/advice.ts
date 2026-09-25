/** Human action sentences ("숫자 → 의미 → 행동"). Built only from the deterministic analysis — no new facts. */
import { price } from "./format";

export interface AdviceInput {
  action: string;
  price: number | null;
  maxBuy?: number | null;
  idealEntry?: number | null;
  stop?: number | null;
  rr?: number | null;
  eventRisk?: string | null;
  vetoes?: string[];
  sizeLimit?: string | null;
  status?: string | null; // CURRENT | AGING | EXPIRED
  sectorKnown?: boolean;
  held?: boolean;
}

export function priceZone(a: AdviceInput): { text: string; tone: "pos" | "warn" | "neg" | "neutral" } {
  if (a.price === null || a.maxBuy === null || a.maxBuy === undefined) return { text: "가격 계획 없음", tone: "neutral" };
  if (a.stop != null && a.price <= a.stop) return { text: "손절가 아래 — 매수 근거 훼손", tone: "neg" };
  if (a.price <= a.maxBuy) return { text: "적정 매수 구간 안", tone: "pos" };
  return { text: `최대 매수가 ${price(a.maxBuy)} 초과 — 추격 구간`, tone: "warn" };
}

/** One main sentence plus optional supporting sentences. */
export function advise(a: AdviceInput): { headline: string; details: string[] } {
  const d: string[] = [];
  const v = a.vetoes ?? [];
  if (a.status && a.status !== "CURRENT" && ["BUY", "BUY SMALL", "ADD"].includes(a.action)) {
    return { headline: "추천 이후 시간이 지나 지금 가격 기준으로는 유효하지 않습니다. ‘분석 다시하기’를 눌러 최신 판단을 확인하세요.", details: [] };
  }
  if (a.eventRisk === "HIGH" || a.eventRisk === "EXTREME") d.push("실적 발표 등 중요한 이벤트가 가까워 단기 변동이 클 수 있습니다.");
  if (a.sizeLimit === "SMALL" || a.sizeLimit === "WATCH") d.push("포트폴리오의 업종·테마 쏠림이나 현금 비중 때문에 비중이 제한됩니다.");
  if (a.sectorKnown === false) d.push("업종 분류가 불확실해 일반 모델로 평가했습니다. 신뢰도를 낮춰 보세요.");
  switch (a.action) {
    case "BUY":
      return { headline: `매수 조건을 충족했습니다. 최대 매수가 ${price(a.maxBuy ?? null)} 이하에서만 유효하고, 손절가는 ${price(a.stop ?? null)}입니다.`, details: d };
    case "BUY SMALL":
      if (a.eventRisk === "HIGH" || a.eventRisk === "EXTREME") return { headline: "좋은 종목이지만 중요 이벤트가 가까워 비중을 작게 시작하는 편이 좋습니다.", details: d };
      if (a.sizeLimit) return { headline: "매력은 있지만 포트폴리오 쏠림 때문에 소액만 고려하는 것이 좋습니다.", details: d };
      return { headline: "매력은 있지만 최상위 기준에는 조금 못 미쳐, 작은 비중으로 시작하는 것이 적절합니다.", details: d };
    case "ADD":
      return { headline: `보유 중이며 추가매수 구간입니다. 손절가 ${price(a.stop ?? null)}를 지키는 선에서 비중을 늘릴 수 있습니다.`, details: d };
    case "WAIT":
      if (v.includes("THESIS_INVALIDATED")) return { headline: "투자 논리를 깨는 조건이 발생해 지금은 진입하지 않는 것이 좋습니다.", details: d };
      if (v.includes("EXTREME_EVENT_RISK")) return { headline: "결과가 크게 갈리는 이벤트를 앞두고 있어, 이벤트 이후로 판단을 미루는 것이 좋습니다.", details: d };
      if (v.includes("UNACCEPTABLE_LIQUIDITY")) return { headline: "거래량이 적어 원하는 가격에 사고팔기 어렵습니다. 대기하세요.", details: d };
      if (a.price !== null && a.maxBuy != null && a.price > a.maxBuy) {
        return { headline: `지금은 추격 매수보다 ${price(a.idealEntry ?? a.maxBuy)} 근처까지의 눌림을 기다리는 편이 유리합니다.`, details: d };
      }
      if (a.rr != null && a.rr < 2) return { headline: "종목은 괜찮지만 지금 가격에서는 기대수익 대비 위험(손익비)이 부족합니다. 기다리세요.", details: d };
      return { headline: "매력은 있지만 지금 들어갈 근거가 부족합니다. 조건이 맞을 때까지 기다리세요.", details: d };
    case "WATCH":
      return { headline: "아직 매수할 만큼 매력적이지 않습니다. 관심 종목으로 지켜보세요.", details: d };
    case "HOLD":
      return { headline: "보유를 유지하세요. 팔 이유도, 더 살 이유도 아직은 뚜렷하지 않습니다.", details: d };
    case "REDUCE":
      return { headline: "점수가 보유 기준 아래로 내려갔습니다. 비중을 줄이는 것을 고려하세요.", details: d };
    case "SELL":
      return { headline: "점수 급락·투자 논리 훼손·손절가 이탈 중 하나가 발생했습니다. 매도를 고려하세요.", details: d };
    case "DATA INSUFFICIENT":
      return { headline: "핵심 데이터가 부족하거나 오래되어 지금은 매수·매도 판단을 강하게 내리지 않습니다.", details: ["아래 ‘부족하거나 오래된 데이터’에서 어떤 데이터가 문제인지 확인하세요."] };
    default:
      return { headline: "판단 정보가 없습니다.", details: d };
  }
}
