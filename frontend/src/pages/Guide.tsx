import { Card } from "../components/ui";
import { ACTION_INFO, QUALITY_INFO, STATUS_INFO } from "../i18n";

export default function Guide() {
  return (
    <div className="grid">
      <h1>용어 · 판정 설명</h1>
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
