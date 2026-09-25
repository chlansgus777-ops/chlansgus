import { useState } from "react";
import CalendarPage from "./Calendar";
import Issues from "./Issues";

export default function IssuesCalendar({ initial = "issues" }: { initial?: "issues" | "calendar" }) {
  const [tab, setTab] = useState(initial);
  return (
    <div>
      <div className="page-head"><div><h1>이슈 · 캘린더</h1><div className="t-sub">뉴스를 ‘사건’으로 정리해 어떤 종목에 어떤 경로로 영향을 주는지, 앞으로 어떤 일정이 있는지 봅니다.</div></div></div>
      <div className="tabs" role="tablist">
        <button className={tab === "issues" ? "on" : ""} onClick={() => setTab("issues")}>현재 이슈</button>
        <button className={tab === "calendar" ? "on" : ""} onClick={() => setTab("calendar")}>다가오는 일정</button>
      </div>
      {tab === "issues" ? <Issues /> : <CalendarPage />}
    </div>
  );
}
