import { Card } from "../components/ui";
import { ACTION_INFO, QUALITY_INFO, STATUS_INFO } from "../i18n";
import { STATE_KO, type QuoteState } from "../quotes";

const QUOTE_HELP: Record<QuoteState, string> = {
  LIVE: "스트림으로 방금(60초 이내) 체결이 들어왔습니다. 가격 옆 초록 점.",
  QUIET: "스트림은 연결돼 있지만 이 종목의 체결이 60초 넘게 없습니다(거래가 뜸한 종목). 가격은 마지막 체결가입니다.",
  DELAYED: "스트림이 아닌 REST 조회값입니다. 실시간이 아닐 수 있어 ‘지연’으로 표시합니다.",
  CLOSED_LAST: "장이 닫혀 있습니다. 가격은 마지막 체결가이며 체결 시각을 함께 보여줍니다.",
  EXTENDED_NO_TRADE: "장전(04:00~09:30 ET)·시간외(16:00~20:00 ET)에 아직 체결이 없습니다. 가격은 직전 세션의 값이며 지우지 않습니다.",
  RECONNECTING: "연결이 끊겨 다시 연결하는 중입니다. 가격은 마지막 값과 그 실제 시각 그대로 둡니다.",
  NO_DATA: "아직 받은 시세가 없거나 조회에 실패했습니다.",
  OVER_LIMIT: "동시 구독 한도(무료 계정 50개)를 넘어 스트림에서 빠진 종목입니다. 보고 있는 종목 → 보유 → 관심 순으로 우선합니다.",
  UNAVAILABLE: "시세 스트림이 설정되지 않았습니다(Finnhub 키 없음 또는 모의 모드).",
};

export default function Guide() {
  return (
    <div className="grid">
      <div className="page-head enter"><div><h1>용어 · 도움말</h1><div className="t-sub">화면에 나오는 판단·상태·숫자 표기의 뜻입니다. 기술 용어는 화면에서 ⓘ를 눌러도 볼 수 있습니다.</div></div></div>
      <Card title="추천 행동 (Action)">
        <table><thead><tr><th>표시</th><th>뜻</th></tr></thead>
          <tbody>{Object.entries(ACTION_INFO).map(([k, v]) => <tr key={k}><td><span className={`badge a-${k.replace(/ /g, "-")}`}>{v.label}({k})</span></td><td style={{ whiteSpace: "normal" }}>{v.help}</td></tr>)}</tbody></table>
      </Card>
      <Card title="데이터 상태 (Data Quality)">
        <table><thead><tr><th>표시</th><th>뜻</th></tr></thead>
          <tbody>{Object.entries(QUALITY_INFO).map(([k, v]) => <tr key={k}><td className={`q-${k}`}>{v.label}({k})</td><td style={{ whiteSpace: "normal" }}>{v.help}</td></tr>)}</tbody></table>
      </Card>
      <Card title="저장된 추천의 현재 유효성">
        <table><tbody>{Object.entries(STATUS_INFO).map(([k, v]) => <tr key={k}><td><span className={`status s-${k}`}>{v.label}</span></td><td style={{ whiteSpace: "normal" }}>{v.help}</td></tr>)}</tbody></table>
        <div className="muted">‘추천 당시 최신(FRESH)’과 ‘지금도 최신’은 다릅니다. 추천 이후 새 거래일이 마감되면 가격 계획을 그대로 쓰지 말고 다시 분석하세요.</div>
      </Card>
      <Card title="현재가 옆의 시세 상태">
        <table><thead><tr><th>표시</th><th>뜻</th></tr></thead>
          <tbody>{(Object.keys(QUOTE_HELP) as QuoteState[]).map((k) => <tr key={k}><td style={{ whiteSpace: "nowrap" }}>{STATE_KO[k]}</td><td style={{ whiteSpace: "normal" }}>{QUOTE_HELP[k]}</td></tr>)}</tbody></table>
        <div className="muted">화면의 현재가는 참고용입니다. 분석 기준가(분석에 쓴 가격·시각)는 따로 표시되며, 매수 판단과 수량 계산은 현재가가 20분 이내일 때만 다시 확인합니다. 시세가 들어온다고 분석이 다시 실행되지는 않습니다.</div>
      </Card>
      <Card title="숫자 · 시간 표기">
        <ul className="list">
          <li>가격과 금액은 모두 미국 달러(USD)입니다. 원화(₩) 환산은 FRED 원/달러 환율이 있을 때만 참고용으로 표시합니다.</li>
          <li>큰 금액은 $2.55T 처럼 표시하고, 괄호 안에 ‘약 2조 5,500억 달러’처럼 한국식 단위를 함께 보여줍니다.</li>
          <li>시각은 미국 동부시간(ET)을 먼저, 한국시간(KST)을 괄호 안에 함께 표시합니다(YYYY-MM-DD HH:mm).</li>
          <li>신뢰도는 판정의 견고성(데이터 완결성·기준선과의 거리)이며 성공 확률이 아닙니다.</li>
          <li>손익비 = (1차 목표가 − 현재가) ÷ (현재가 − 손절가). 최대 매수가는 손익비 2가 되는 가격입니다.</li>
          <li>가격·수익률 같은 숫자는 모든 숫자의 폭이 같은 글꼴 설정(tabular-nums)으로 표시해, 값이 바뀌어도 자리가 흔들리지 않습니다.</li>
        </ul>
      </Card>
      <Card title="글꼴 · 라이선스">
        <div className="explain">화면 글꼴은 앱에 포함된 Pretendard Variable(Copyright © 2021 Kil Hyung-jin, SIL Open Font License 1.1)입니다. PC에 글꼴을 설치하지 않아도 같은 모습으로 보입니다. <a href="./licenses/Pretendard-OFL-1.1.txt" target="_blank" rel="noreferrer">라이선스 전문 보기</a></div>
      </Card>
    </div>
  );
}
