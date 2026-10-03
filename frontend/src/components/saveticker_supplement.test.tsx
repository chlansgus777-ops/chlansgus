// @vitest-environment jsdom
import {act,cleanup,render,screen,waitFor} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {SaveTickerSupplement,SaveTickerNews} from "./SaveTickerSupplement";
import {resetApiCache} from "./useApi";
afterEach(()=>{cleanup();resetApiCache();vi.unstubAllGlobals();vi.useRealTimers();});

it("keeps optional data hidden until a browser is enrolled",async()=>{
  const fetcher=vi.fn(async()=>new Response(JSON.stringify({enabled:false,resources:{}})));
  vi.stubGlobal("fetch",fetcher);render(<SaveTickerSupplement resource="calendar"/>);
  await waitFor(()=>expect(fetcher).toHaveBeenCalled());
  expect(screen.queryByRole("region")).toBeNull();
});

it("separates provider earnings, unknown fiscal year, and source AI summary",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>new Response(JSON.stringify({enabled:true,resources:{details:{rows:[{news_id:"x",title:"실적",published_at:"2026-10-01T01:00:00Z",collected_at:"2026-10-01T02:00:00Z",related_companies:[{ticker:"MU",name:"Micron"}],provider_summary:"공급자 AI 요약",source_text:"공급자 원문",earnings:{period_label:"4분기",fiscal_year:null,basis:"adjusted",eps:{actual:33.42,estimate:31.83,surprise_fraction:0.05,source_text:"실적 자료"}}}],last_success:"2026-10-01T02:00:00Z",stale:false,error:null}}}))));
  render(<SaveTickerSupplement resource="details" ticker="MU"/>);
  await waitFor(()=>expect(screen.getByText(/회계연도 미확인/)).toBeTruthy());
  expect(screen.getByText("SaveTicker 제공 요약")).toBeTruthy();
  expect(screen.getByText(/EPS 실제 33.42 \/ 예상 31.83/)).toBeTruthy();
  expect(screen.queryByText(/지금 매수/)).toBeNull();
});

it("preserves partial stale calendar with explicit date precision and failure",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>new Response(JSON.stringify({enabled:true,resources:{calendar:{rows:[{event_id:"x",title:"일정",event_date:"2026-10-02",scheduled_at:null,date_only:true,collected_at:"2026-10-01T02:00:00Z"}],last_success:"2026-10-01T02:00:00Z",stale:true,error:"ACCESS_DENIED"}}}))));
  render(<SaveTickerSupplement resource="calendar"/>);
  await waitFor(()=>expect(screen.getByRole("status").textContent).toContain("접근이 거절"));
  expect(screen.getByText(/시각 미제공/)).toBeTruthy();
  expect(screen.getByRole("status").textContent).toContain("이전 값");
});

it("does not label an unspecified earnings basis as adjusted or an empty report as read",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>new Response(JSON.stringify({enabled:true,resources:{
    details:{rows:[{news_id:"acn",title:"액센츄어 실적",earnings:{period_label:"4분기",basis:"unspecified",eps:{actual:3.29,estimate:null,surprise_fraction:null,source_text:"실제 EPS"},revenue:{actual:18700000000,estimate:18040000000,surprise_fraction:0.0366,source_text:"실적"}}}],stale:false,last_success:null,error:null},
    reports:{rows:[{report_id:"661",title:"리포트",source_text:"",has_readable_text:false,provider_url:"https://saveticker.com/report/661"}],stale:false,last_success:null,error:null}
  }}))));
  render(<><SaveTickerSupplement resource="details"/><SaveTickerSupplement resource="reports"/></>);
  await waitFor(()=>expect(screen.getByText(/조정 여부 미명시/)).toBeTruthy());
  expect(screen.queryByText(/조정 기준/)).toBeNull();
  expect(screen.getByText(/EPS 실제 3.29 \/ 예상 미제공 · 서프라이즈 계산 불가/)).toBeTruthy();
  expect(screen.getByText(/읽을 수 있는 본문은 아직 수집되지/)).toBeTruthy();
  expect(screen.getByRole("link",{name:"SaveTicker 리포트 열기"}).getAttribute("href")).toBe("https://saveticker.com/report/661");
});

it("shows option aggregate dates separately from receipt time and keeps absent metrics missing",async()=>{
  vi.stubGlobal("fetch",vi.fn(async()=>new Response(JSON.stringify({enabled:true,resources:{options:{rows:[{option_id:"mu",title:"MU 옵션 집계",dates:{snapshotDate:"2026-09-30",batchDate:"2026-09-30"},snapshot_prior_day:true,batch_prior_day:true,metrics:{referencePrice:1065.11,volume:null}}],last_success:"2026-10-01T02:00:00Z",stale:false,error:null}}}))));
  render(<SaveTickerSupplement resource="options"/>);
  await waitFor(()=>expect(screen.getByText(/스냅샷 2026-09-30 · 전일 자료/)).toBeTruthy());
  expect(screen.getByText(/실시간 체결가가 아닙니다/)).toBeTruthy();
  expect(screen.getByText("1,065.11")).toBeTruthy();
  expect(screen.getAllByText("미제공").length).toBeGreaterThan(0);
  expect(screen.queryByText("CURRENT")).toBeNull();
});

it("shows a news article once when its detail is available and preserves headline-only articles",async()=>{
  vi.stubGlobal("fetch",vi.fn(async(input)=>new Response(JSON.stringify(String(input).includes("/saveticker/supplement")?
    {enabled:true,resources:{details:{rows:[{news_id:"a",title:"요약 있는 기사",provider_summary:"제공 요약"},{news_id:"b",title:"제목만 있는 기사"}],last_success:null,stale:false,error:null}}}:
    {enabled:true,rows:[{news_id:"a",title:"요약 있는 기사",published_at:"2026-10-01T01:00:00Z",source:"reuters",url:"",tickers:[]},{news_id:"b",title:"제목만 있는 기사",published_at:"2026-10-01T01:00:00Z",source:"reuters",url:"",tickers:[]}]}))));
  render(<SaveTickerNews/>);
  await waitFor(()=>expect(screen.getByText("SaveTicker 제공 요약")).toBeTruthy());
  expect(screen.getAllByText("요약 있는 기사")).toHaveLength(1);
  expect(screen.getAllByText("제목만 있는 기사")).toHaveLength(1);
});

it("discovers enrollment from another browser without remounting a disabled view",async()=>{
  vi.useFakeTimers();
  let enabled=false;
  const fetcher=vi.fn(async()=>new Response(JSON.stringify({enabled,resources:enabled?{calendar:{rows:[{event_id:"remote",title:"다른 브라우저에서 수신한 일정",event_date:"2026-10-02",date_only:true}],last_success:"2026-10-01T02:00:00Z",stale:false,error:null}}:{}})));
  vi.stubGlobal("fetch",fetcher);
  render(<SaveTickerSupplement resource="calendar"/>);
  await act(async()=>{await vi.advanceTimersByTimeAsync(0);});
  expect(screen.queryByRole("region")).toBeNull();
  enabled=true;
  await act(async()=>{await vi.advanceTimersByTimeAsync(60_000);});
  expect(screen.getByText("다른 브라우저에서 수신한 일정")).toBeTruthy();
  expect(fetcher).toHaveBeenCalledTimes(2);
});
