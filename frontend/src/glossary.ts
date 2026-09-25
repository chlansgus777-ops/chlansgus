/** Plain-language glossary. Every technical term shown in the UI should go through <Term k="…"> so a beginner
 * gets: name, a one-line meaning, why it matters, and which direction is good. See docs/COPYWRITING_GUIDE.md */

export type Dir = "low_good" | "high_good" | "neutral";
export interface Entry { name: string; short: string; why?: string; dir?: Dir }

export const GLOSSARY: Record<string, Entry> = {
  score: { name: "점수", short: "펀더멘털·밸류에이션·실적·이슈·거시·위험·진입가를 합친 0~100점 종합 평가", why: "80점 이상이면 매수, 72점 이상이면 소액 매수 후보", dir: "high_good" },
  confidence: { name: "신뢰도", short: "데이터가 얼마나 충분하고, 판정이 기준선에서 얼마나 떨어져 있는지", why: "성공 확률이 아니라 ‘이 판단이 흔들리지 않을 정도’입니다", dir: "high_good" },
  ideal_entry: { name: "이상적 진입가", short: "지지선 근처의 가장 유리한 매수 가격", dir: "neutral" },
  max_buy: { name: "최대 매수가", short: "이 가격을 넘으면 목표가 대비 위험이 커져 추격 매수가 됩니다", why: "손익비 2가 되는 가격에서 거꾸로 계산", dir: "neutral" },
  add_zone: { name: "추가매수 구간", short: "이미 보유했다면 더 사도 되는 가격대", dir: "neutral" },
  stop: { name: "손절가", short: "이 가격 아래로 마감하면 매수 근거가 깨졌다고 보고 정리하는 가격", why: "손실을 미리 정해 두는 안전장치", dir: "neutral" },
  target: { name: "목표가", short: "가까운 저항선 등으로 계산한 1차·2차 이익 실현 가격", dir: "neutral" },
  rr: { name: "손익비", short: "(목표가−현재가) ÷ (현재가−손절가). 2면 1을 잃을 위험에 2를 벌 기회", why: "2 미만이면 매수하지 않습니다", dir: "high_good" },
  trailing_pe: { name: "PER(과거 12개월)", short: "주가 ÷ 지난 1년 주당순이익. 낮을수록 이익 대비 싸다는 뜻", why: "업종마다 적정 수준이 달라 같은 업종끼리 비교해야 합니다", dir: "low_good" },
  forward_pe: { name: "선행 PER", short: "앞으로 예상 이익 기준 주가 수준. 낮을수록 상대적으로 저렴할 수 있음", why: "성장주는 높게, 경기민감주는 낮게 나오는 경향", dir: "low_good" },
  peg: { name: "PEG", short: "PER을 이익 성장률로 나눈 값. 1 안팎이면 성장 대비 적정", why: "비싸 보여도 성장이 빠르면 괜찮을 수 있는지 보는 지표", dir: "low_good" },
  price_sales: { name: "PSR(주가매출비율)", short: "시가총액 ÷ 연매출. 이익이 없는 성장 기업 평가에 사용", dir: "low_good" },
  ev_sales: { name: "EV/매출", short: "기업가치(시총+순부채) ÷ 연매출", dir: "low_good" },
  ev_ebitda: { name: "EV/EBITDA", short: "기업가치 ÷ 현금성 영업이익. 부채까지 반영한 가격 수준", dir: "low_good" },
  price_fcf: { name: "P/FCF", short: "시가총액 ÷ 잉여현금흐름", dir: "low_good" },
  fcf_yield: { name: "FCF 수익률", short: "잉여현금흐름 ÷ 시가총액. 높을수록 주가 대비 현금을 많이 범", dir: "high_good" },
  earnings_yield: { name: "이익수익률", short: "주당순이익 ÷ 주가 (PER의 역수)", dir: "high_good" },
  forward_earnings_yield: { name: "선행 이익수익률", short: "예상 주당순이익 ÷ 주가", dir: "high_good" },
  p_b: { name: "PBR", short: "시가총액 ÷ 순자산. 은행·보험 평가에 중요", dir: "low_good" },
  p_tbv: { name: "P/TBV", short: "시가총액 ÷ 유형 순자산(영업권 제외). 은행 평가의 핵심", dir: "low_good" },
  p_ffo: { name: "P/FFO", short: "리츠(부동산) 평가 배수. 주가 ÷ 운영자금(FFO)", dir: "low_good" },
  dividend_yield: { name: "배당수익률", short: "연간 배당금 ÷ 주가", dir: "high_good" },
  market_cap: { name: "시가총액", short: "주가 × 발행주식수. 회사 전체의 시장 가격", dir: "neutral" },
  enterprise_value: { name: "기업가치(EV)", short: "시가총액 + 부채 − 현금", dir: "neutral" },
  drawdown: { name: "낙폭(Drawdown)", short: "최고점 대비 얼마나 떨어졌는지", why: "견뎌야 할 최대 손실 크기를 알려줍니다", dir: "high_good" },
  max_drawdown: { name: "최대 낙폭(MDD)", short: "기간 중 최고점에서 최저점까지 가장 크게 떨어진 비율", dir: "high_good" },
  iv_rank: { name: "IV 순위", short: "옵션 변동성 수준(0~1). 높으면 단기 변동 가능성이 큼", dir: "neutral" },
  expected_move: { name: "예상 변동폭", short: "옵션 가격이 암시하는 만기까지의 예상 주가 변동 범위", dir: "neutral" },
  rotce: { name: "ROTCE", short: "유형 자기자본 이익률. 은행이 자본으로 얼마나 잘 버는지", dir: "high_good" },
  cet1: { name: "CET1 비율", short: "은행의 핵심 자기자본 비율. 높을수록 위기에 강함", dir: "high_good" },
  nim: { name: "순이자마진(NIM)", short: "은행이 대출·예금 금리 차로 버는 마진", dir: "high_good" },
  affo: { name: "AFFO", short: "리츠가 실제 배당에 쓸 수 있는 현금(조정 운영자금)", dir: "high_good" },
  ffo: { name: "FFO", short: "리츠의 운영자금(순이익 + 감가상각 등)", dir: "high_good" },
  rsi14: { name: "RSI(14)", short: "최근 14일 상승·하락 강도. 70 이상 과열, 30 이하 과매도로 봄", dir: "neutral" },
  atr14: { name: "ATR(14)", short: "하루 평균 가격 변동폭. 손절가·목표가 간격 계산에 사용", dir: "neutral" },
  sma: { name: "이동평균", short: "최근 N일 종가 평균. 추세 방향과 지지선 판단에 사용", dir: "neutral" },
  anchored_vwap: { name: "앵커드 VWAP", short: "특정 시점 이후 거래량 가중 평균 가격. 평균 매수 단가 추정", dir: "neutral" },
  rs_6m: { name: "6개월 상대강도", short: "최근 6개월 수익률에서 S&P500 수익률을 뺀 값", dir: "high_good" },
  volume_ratio: { name: "거래량 배수", short: "최근 거래량 ÷ 평소 거래량. 1보다 크면 관심 증가", dir: "neutral" },
  short_interest: { name: "공매도 잔고 비율", short: "하락에 베팅한 주식 수의 비율. 높으면 변동성·악재 민감도가 큼", dir: "low_good" },
  hhi: { name: "HHI(집중도)", short: "보유 비중 제곱합. 0에 가까울수록 분산, 1에 가까울수록 한 종목에 집중", dir: "low_good" },
  beta: { name: "베타", short: "시장(SPY)이 1% 움직일 때 포트폴리오가 평균 몇 % 움직이는지", dir: "neutral" },
  correlation: { name: "상관계수", short: "두 종목이 같이 움직이는 정도(−1~1). 0.75 이상이면 사실상 같은 베팅", dir: "low_good" },
  eps: { name: "EPS(주당순이익)", short: "순이익 ÷ 주식 수", dir: "high_good" },
  revision: { name: "추정치 리비전", short: "애널리스트 이익 추정치가 최근 올라가는지(상향) 내려가는지(하향)", why: "주가는 ‘좋은 숫자’보다 ‘예상보다 좋아지는 숫자’에 반응합니다", dir: "high_good" },
  surprise: { name: "실적 서프라이즈", short: "실제 실적이 시장 예상보다 얼마나 좋았는지/나빴는지", dir: "high_good" },
  guidance: { name: "가이던스", short: "회사가 스스로 제시한 다음 분기·연간 전망", dir: "high_good" },
  priced_in: { name: "선반영", short: "이 뉴스가 이미 주가에 얼마나 반영되었는지 추정(0~100)", why: "많이 반영됐다면 좋은 뉴스여도 추가 상승 여지가 작습니다", dir: "low_good" },
  regime: { name: "시장 국면", short: "지금 시장이 어떤 성격의 환경인지(위험 선호, 금리 충격 등)", dir: "neutral" },
  data_quality: { name: "데이터 품질", short: "판단에 쓴 데이터가 최신인지, 부족하거나 오래됐는지", dir: "neutral" },
  win_rate: { name: "승률", short: "청산된 모의 거래 중 수익으로 끝난 비율", dir: "high_good" },
  profit_factor: { name: "손익 팩터", short: "총이익 ÷ 총손실. 1보다 크면 번 돈이 잃은 돈보다 많음", dir: "high_good" },
  ic: { name: "IC(정보계수)", short: "점수가 높을수록 실제 수익률도 높았는지 보는 순위 상관(−1~1)", why: "0.05 이상이 꾸준하면 의미 있는 신호로 봅니다", dir: "high_good" },
  gross_margin: { name: "매출총이익률", short: "매출에서 원가를 뺀 비율. 가격 결정력의 지표", dir: "high_good" },
  operating_margin: { name: "영업이익률", short: "매출 대비 영업이익 비율", dir: "high_good" },
  fcf_margin: { name: "FCF 마진", short: "매출 대비 잉여현금흐름 비율", dir: "high_good" },
  roic: { name: "ROIC", short: "투하자본이익률. 사업에 넣은 돈으로 얼마나 버는지", dir: "high_good" },
  revenue_growth_yoy: { name: "매출 성장률(전년비)", short: "같은 분기 작년 대비 매출 증가율", dir: "high_good" },
  eps_growth_yoy: { name: "EPS 성장률(전년비)", short: "같은 분기 작년 대비 주당순이익 증가율", dir: "high_good" },
  net_debt_to_ebitda: { name: "순부채/EBITDA", short: "빚을 현금성 이익 몇 년치로 갚을 수 있는지. 낮을수록 안전", dir: "low_good" },
  session: { name: "거래 세션", short: "프리마켓(04:00~09:30 ET), 정규장(09:30~16:00), 애프터마켓(16:00~20:00)", dir: "neutral" },
};

export const DIR_KO: Record<Dir, string> = { low_good: "낮을수록 유리", high_good: "높을수록 유리", neutral: "방향보다 맥락이 중요" };

export function tip(k: string): string {
  const e = GLOSSARY[k];
  if (!e) return "";
  return [e.short, e.why ? `왜 중요? ${e.why}` : "", e.dir ? `(${DIR_KO[e.dir]})` : ""].filter(Boolean).join("\n");
}
