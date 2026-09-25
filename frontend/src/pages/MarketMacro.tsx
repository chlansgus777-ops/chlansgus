import { useState } from "react";
import Macro from "./Macro";
import Market from "./Market";

export default function MarketMacro() {
  const [tab, setTab] = useState<"market" | "macro">("market");
  return (
    <div>
      <div className="page-head"><div><h1>시장 · 거시 경제</h1><div className="t-sub">지금 시장 분위기와 금리·물가·환율이 종목에 어떤 바람을 만드는지 봅니다.</div></div></div>
      <div className="tabs" role="tablist">
        <button className={tab === "market" ? "on" : ""} onClick={() => setTab("market")}>시장 분위기</button>
        <button className={tab === "macro" ? "on" : ""} onClick={() => setTab("macro")}>거시 지표</button>
      </div>
      {tab === "market" ? <Market /> : <Macro />}
    </div>
  );
}
