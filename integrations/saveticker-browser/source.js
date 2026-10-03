// Runs in the browser's isolated content-script world, on the one approved source origin.
// fetch uses the browser's existing session automatically; no credential/session API is read.
const SYMBOL = /^[A-Z0-9][A-Z0-9.\-]{0,14}$/;
// Options are read for the symbols MarketLens asks for (its candidates), never for names it did not choose.
const validSymbols = (list) => Array.isArray(list) && list.length <= 10 && list.every((s) => typeof s === "string" && SYMBOL.test(s));
async function readMarketResource(resource, symbols) {
  const cancel = new AbortController();
  const timer = setTimeout(() => cancel.abort(), 35_000);
  const read = async (path) => {
    const r = await fetch(path, {credentials:"same-origin",headers:{Accept:"application/json"},signal:cancel.signal});
    if (!r.ok) throw new Error(r.status===401?"AUTH_REQUIRED":r.status===403?"ACCESS_DENIED":"NETWORK");
    return r.json();
  };
  const selectBlocks = (value) => {
    if (typeof value === "string") return value.slice(0,12_000);
    if (!Array.isArray(value) || value.length>200) return null;
    let remaining=12_000;
    return value.filter(b=>["text","paragraph","html","heading","list"].includes(b.type)).map(b=>{
      const content=typeof b.content==="string"?b.content.slice(0,remaining):"";
      remaining-=content.length; return {type:b.type,content};
    });
  };
  try {
    if (resource === "calendar") {
      const now=new Date(), start=new Date(now.getTime()-86400000),end=new Date(now.getTime()+31*86400000);
      const data=await read(`/api/calendar/events?start_date=${start.toISOString().slice(0,10)}&end_date=${end.toISOString().slice(0,10)}`);
      if (!Array.isArray(data.events)||data.events.length>200) throw new Error("FORMAT");
      return {resource,payload:{events:data.events.map(r=>({id:r.id,title:r.title,event_date:r.event_date,event_date_only:r.event_date_only,content:selectBlocks(r.content)}))}};
    }
    if (resource === "reports") {
      const data=await read("/api/report/list?page=1&page_size=20");
      if (!Array.isArray(data.reports)) throw new Error("FORMAT");
      const reports=[];
      for (const item of data.reports.slice(0,3)) {
        const detail=await read("/api/report/detail?id="+encodeURIComponent(item.id));
        const r=detail.report;
        if (!r||typeof r!=="object") throw new Error("FORMAT");
        reports.push({id:r.id,title:r.title,created_at:r.created_at,content:selectBlocks(r.content)});
      }
      return {resource,payload:{reports}};
    }
    if (resource === "options") {
      if (!validSymbols(symbols)) throw new Error("FORMAT");
      const options=[];
      for (const symbol of symbols) {
        const r=await read("/api/stocks/api/v1/tickers/"+encodeURIComponent(symbol)+"/options");
        if (!r || typeof r!=="object") throw new Error("FORMAT");
        const selected={};
        for (const key of ["symbol","optionable","snapshotDate","snapshotIsPriorDay","batchDate","batchIsPriorDay","nearestExpiry","maxPain","volume","putCallRatioVolume","putCallRatioOpenInterest","referencePrice","netGammaExposure","gammaPer1Pct","callWall","putWall","gammaFlip"]) selected[key]=r[key];
        options.push(selected);
      }
      return {resource,payload:{options}};
    }
    if (resource !== "details") throw new Error("FORMAT");
    const latest=await read("/api/news/list?page=1&page_size=20&sort=created_at_desc");
    const earnings=await read("/api/news/list?page=1&page_size=20&search="+encodeURIComponent("실적발표"));
    if (!Array.isArray(latest.news_list)||!Array.isArray(earnings.news_list)) throw new Error("FORMAT");
    const ids=[...new Set([...latest.news_list.slice(0,3),...earnings.news_list.slice(0,2)].map(r=>r.id))];
    const details=[];
    for (const id of ids) {
      if (typeof id!=="string"&&typeof id!=="number") throw new Error("FORMAT");
      const r=await read("/api/news/detail?id="+encodeURIComponent(id));
      let translations=null;
      if (r.translations?.translated) {
        const selected={};
        for (const locale of ["ko_KR","ko","ko-KR",r.translations.source_locale]) {
          const v=r.translations.translated[locale];
          if (v) selected[locale]={summary:selectBlocks(v.summary)};
        }
        translations={source_locale:r.translations.source_locale,translated:selected};
      }
      details.push({id:r.id,title:r.title,created_at:r.created_at,content:selectBlocks(r.content),translations,
        tickers:Array.isArray(r.tickers)?r.tickers.map(t=>({symbol:t.symbol,name:t.name})):[],
        extra:r.extra?{source_url:r.extra.source_url,source_created_at:r.extra.source_created_at}:null,
        vote_stats:r.vote_stats?{vote_counts:r.vote_stats.vote_counts}:null});
    }
    return {resource,payload:{details}};
  } catch (e) { return {resource,error:["AUTH_REQUIRED","ACCESS_DENIED","FORMAT"].includes(e.message)?e.message:"NETWORK"}; }
  finally { clearTimeout(timer); }
}

if (!globalThis.__marketlensSourceInstalled) {
  globalThis.__marketlensSourceInstalled = true;
  chrome.runtime.onMessage.addListener((message, sender, respond) => {
    if (sender.id !== chrome.runtime.id) return;
    if (message?.type === "READ_SAVETICKER_EXTRA") {
      readMarketResource(message.resource, message.symbols).then(respond);
      return true;
    }
    if (message?.type !== "READ_SAVETICKER_NEWS") return;
    (async () => {
      const cancel = new AbortController();
      const timer = setTimeout(() => cancel.abort(), 10_000);
      try {
        const r = await fetch("/api/news/list?page=1&page_size=20&sort=created_at_desc", {
          credentials: "same-origin", headers: {Accept: "application/json"}, signal: cancel.signal,
        });
        if (r.status === 401) return {error: "AUTH_REQUIRED"};
        if (r.status === 403) return {error: "ACCESS_DENIED"};
        if (!r.ok) return {error: "NETWORK"};
        const body = await r.json();
        if (!Array.isArray(body?.news_list) || body.news_list.length > 100) return {error: "FORMAT"};
        return {news: {news_list: body.news_list.map((row) => ({
          id: row.id, title: row.title, created_at: row.created_at, source: row.source,
          tag_names: row.tag_names, view_count: row.view_count,
        }))}}; // user_info, article bodies and other account fields never leave this page
      } catch { return {error: "NETWORK"}; }
      finally { clearTimeout(timer); }
    })().then(respond);
    return true;
  });
  chrome.runtime.sendMessage({type: "SAVETICKER_READY"}).catch(() => {});
}
