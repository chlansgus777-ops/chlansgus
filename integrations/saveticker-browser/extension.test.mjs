import {test} from "node:test";
import assert from "node:assert/strict";
import {readFile} from "node:fs/promises";
import vm from "node:vm";

const sourceText = await readFile(new URL("source.js", import.meta.url), "utf8");

test("normal same-origin news request transfers only the permitted news fields", async () => {
  let handler;
  const requests = [];
  const context = {chrome:{runtime:{id:"extension", onMessage:{addListener:(h)=>{handler=h;}}, sendMessage:async()=>({})}},
    setTimeout, clearTimeout, AbortController, fetch:async(path, opts)=>{
      requests.push({path, opts});
      return {ok:true,status:200,json:async()=>({user_info:{private:"must-not-transfer"},news_list:[{id:1,title:"fixture",created_at:"2026-10-01T10:00:00+09:00",source:"fixture",tag_names:["$MU"],view_count:0,content:"private article body",password:"never-transfer"}]})};
    }};
  vm.runInNewContext(sourceText, context);
  const result = await new Promise((done)=>handler({type:"READ_SAVETICKER_NEWS"},{id:"extension"},done));
  assert.equal(requests[0].path,"/api/news/list?page=1&page_size=20&sort=created_at_desc");
  assert.equal(requests[0].opts.credentials,"same-origin");
  assert.equal(result.news.news_list.length,1);
  assert.equal(JSON.stringify(result).includes("private"),false);
  assert.equal(JSON.stringify(result).includes("password"),false);
  assert.equal(handler({type:"READ_SAVETICKER_NEWS"},{id:"other-extension"},()=>{}),undefined);
  assert.equal(requests.length,1);
});

test("source sign-in failures are distinct from valid empty news", async () => {
  for (const code of [401,403,200]) {
    let handler;
    const context = {chrome:{runtime:{id:"extension",onMessage:{addListener:(h)=>{handler=h;}},sendMessage:async()=>({})}},
      setTimeout,clearTimeout,AbortController,fetch:async()=>({status:code,ok:code===200,json:async()=>({news_list:[]})})};
    vm.runInNewContext(sourceText,context);
    const result = await new Promise((done)=>handler({type:"READ_SAVETICKER_NEWS"},{id:"extension"},done));
    if (code===200) assert.equal(result.news.news_list.length,0);
    else assert.equal(result.error,code===401?"AUTH_REQUIRED":"ACCESS_DENIED");
  }
});

test("supplemental calendar and details use fixed read endpoints without account or user vote export",async()=>{
  let handler;
  const paths=[];
  const context={chrome:{runtime:{id:"extension",onMessage:{addListener:h=>{handler=h;}},sendMessage:async()=>({})}},
    setTimeout,clearTimeout,AbortController,Date,fetch:async(path,opts)=>{
      paths.push(path);assert.equal(opts.credentials,"same-origin");
      const body=path.startsWith("/api/calendar/events")?{events:[{id:1,title:"Fed",event_date:"2026-10-01T23:00:00",event_date_only:false,content:[{type:"text",content:"event"}],author_name:"secret-author",is_notification_enabled:true}]}:
        path.startsWith("/api/news/list")?{news_list:[{id:"one"}]}:
        path.startsWith("/api/stocks/api/v1/tickers/")?{symbol:"MU",optionable:true,snapshotIsPriorDay:true,batchIsPriorDay:true,referencePrice:1065.11,snapshotDate:"2026-09-30",private:"secret-options"}:
        {id:"one",title:"fixture",created_at:"2026-10-01T01:00:00Z",content:[{type:"text",content:"source article"}],tickers:[{symbol:"MU",name:"Micron"}],translations:{source_locale:"en",translated:{ko_KR:{summary:[{type:"text",content:"provider summary"}],user_info:"secret-user"}}},vote_stats:{vote_counts:{positive:2,negative:1},user_vote:"secret-vote"},password:"secret-password",author_id:"secret-id"};
      return {ok:true,status:200,json:async()=>body};
    }};
  vm.runInNewContext(sourceText,context);
  const read=(resource,symbols)=>new Promise(done=>handler({type:"READ_SAVETICKER_EXTRA",resource,symbols},{id:"extension"},done));
  const calendar=await read("calendar");const detail=await read("details");
  assert.equal(calendar.payload.events.length,1);
  assert.equal(detail.payload.details[0].translations.translated.ko_KR.summary[0].content,"provider summary");
  // options only for the symbols MarketLens names (its candidates), never the article's tickers (owner 2026-10-03)
  const unasked=paths.length;
  assert.equal((await read("options")).error,"FORMAT");
  assert.equal((await read("options",["NVDA","bad symbol"])).error,"FORMAT");
  assert.equal(paths.length,unasked);
  const options=await read("options",["MU"]);
  assert.equal(paths.at(-1),"/api/stocks/api/v1/tickers/MU/options");
  assert.equal(options.payload.options[0].snapshotIsPriorDay,true);
  assert.equal(options.payload.options[0].referencePrice,1065.11);
  assert.equal(JSON.stringify([calendar,detail,options]).includes("secret"),false);
  assert.equal(paths.filter(p=>p.startsWith("/api/news/detail?id=")).length,1);
  const before=paths.length;
  assert.equal((await read("profile")).error,"FORMAT");assert.equal(paths.length,before);
});

test("pairing is loopback-only, collection singleflights, and expiry revokes the alarm", async () => {
  let messageHandler, alarmHandler;
  let current, fetches=0, sourceReads=0, cleared=0;
  let finishSource, resolveDelivery;
  const delivery = new Promise((r)=>{resolveDelivery=r;});
  const key="f".repeat(64);
  globalThis.chrome = {
    runtime:{id:"extension",onMessage:{addListener:(h)=>{messageHandler=h;}}},
    storage:{session:{get:async()=>({pair:current}),set:async({pair})=>{current=pair;},remove:async()=>{current=undefined;}}},
    alarms:{create:async()=>{},clear:async()=>{cleared++;},onAlarm:{addListener:(h)=>{alarmHandler=h;}}},
    tabs:{query:async()=>[{id:1,active:true,url:"https://saveticker.com/news"}],sendMessage:async()=>{sourceReads++;return new Promise((r)=>{finishSource=r;});}},
    scripting:{executeScript:async()=>{}},
  };
  const oldFetch=globalThis.fetch;
  globalThis.fetch=async(url,opts)=>{fetches++;assert.equal(url,"http://127.0.0.1:8769/api/saveticker/browser/news");assert.equal(opts.headers["X-MarketLens-News-Bridge"],key);resolveDelivery();return {status:401};};
  try {
    const code=await readFile(new URL("background.js",import.meta.url),"utf8");
    const module=await import("data:text/javascript;base64,"+Buffer.from(code).toString("base64"));
    assert.equal(module.validPair("https://evil.test/api/saveticker/browser/news",key),false);
    assert.equal(module.validPair("http://127.0.0.1:8769/api/portfolio",key),false);
    assert.equal(module.validPair("http://127.0.0.1:99999/api/saveticker/browser/news",key),false);
    let rejected;
    messageHandler({type:"PAIR",endpoint:"http://127.0.0.1:8769/api/saveticker/browser/news",key},{id:"extension",tab:{url:"https://saveticker.com/news"}},(r)=>{rejected=r;});
    assert.equal(rejected.ok,false);
    await new Promise((done)=>messageHandler({type:"PAIR",endpoint:"http://127.0.0.1:8769/api/saveticker/browser/news",key},
      {id:"extension",tab:{url:"http://127.0.0.1:8769/saveticker-bridge.html#key=fixture"}},done));
    // Let the initial pairing reach its pending source read, then simultaneous alarms must share it.
    for(let i=0;i<20 && !finishSource;i++) await Promise.resolve();
    alarmHandler({name:"marketlens-saveticker-news"});alarmHandler({name:"marketlens-saveticker-news"});
    assert.equal(sourceReads,1);
    finishSource({news:{news_list:[]}});
    await delivery;
    for(let i=0;i<20 && !cleared;i++) await Promise.resolve();
    assert.equal(fetches,1);assert.equal(cleared,1);assert.equal(current,undefined);
  } finally {globalThis.fetch=oldFetch;delete globalThis.chrome;}
});

test("supplement cadence survives a cold MV3 worker and repeated pairing", async () => {
  let handler,alarmHandler,delivered;
  const state={pair:{endpoint:"http://127.0.0.1:8769/api/saveticker/browser/news",key:"f".repeat(64)}};
  let extras=0;
  globalThis.chrome={
    runtime:{id:"extension",onMessage:{addListener:h=>{handler=h;}}},
    storage:{session:{get:async()=>({...state}),set:async v=>{Object.assign(state,v);},remove:async names=>{for(const n of [].concat(names))delete state[n];}}},
    alarms:{create:async()=>{},clear:async()=>{},onAlarm:{addListener:h=>{alarmHandler=h;}}},
    tabs:{query:async()=>[{id:1,url:"https://saveticker.com/news",active:true}],sendMessage:async(_id,m)=>{
      if(m.type==="READ_SAVETICKER_NEWS") return {news:{news_list:[]}};
      extras++;return {resource:m.resource,payload:{}};
    }},scripting:{executeScript:async()=>{}}
  };
  const oldFetch=globalThis.fetch;
  globalThis.fetch=async(_url,opts)=>{const message=JSON.parse(opts.body);if(!message.resource||message.resource==="options")delivered?.();
    return {status:200,ok:true,json:async()=>message.resource?{accepted:true}:{accepted:true,option_symbols:["NVDA"]}};};
  const code=await readFile(new URL("background.js",import.meta.url),"utf8");
  const load=async tag=>import("data:text/javascript;base64,"+Buffer.from(code+"\n//"+tag).toString("base64"));
  const settle=async()=>{for(let i=0;i<40;i++)await Promise.resolve();};
  try {
    await load("first-worker");
    let complete=new Promise(r=>{delivered=r;});
    alarmHandler({name:"marketlens-saveticker-news"});
    await complete;await settle();
    assert.equal(extras,4);
    assert.equal(state.supplement.key,state.pair.key);
    const firstTime=state.supplement.at;
    // Model the next two-minute alarm in a freshly loaded worker.
    state.supplement.at=Date.now()-120_000;
    await load("cold-worker");
    complete=new Promise(r=>{delivered=r;});
    alarmHandler({name:"marketlens-saveticker-news"});
    await complete;await settle();
    assert.equal(extras,4);
    assert.notEqual(state.supplement.at,firstTime);
    const retainedTime=state.supplement.at;
    complete=new Promise(r=>{delivered=r;});
    await new Promise(done=>handler({type:"PAIR",...state.pair},{id:"extension",tab:{url:"http://127.0.0.1:8769/saveticker-bridge.html"}},done));
    await complete;await settle();
    assert.equal(extras,4);
    assert.equal(state.supplement.at,retainedTime);
    // An elapsed ten-minute window allows a new supplemental batch.
    state.supplement.at=Date.now()-600_001;
    complete=new Promise(r=>{delivered=r;});
    alarmHandler({name:"marketlens-saveticker-news"});
    await complete;await settle();
    assert.equal(extras,8);
  } finally {globalThis.fetch=oldFetch;delete globalThis.chrome;}
});

test("options are read for the symbols the app names, and not at all when it names none", async () => {
  let alarmHandler;
  const sent=[];let reply={accepted:true,option_symbols:["NVDA","AMD","bad symbol",7]};
  const state={pair:{endpoint:"http://127.0.0.1:8769/api/saveticker/browser/news",key:"f".repeat(64)}};
  globalThis.chrome={
    runtime:{id:"extension",onMessage:{addListener:()=>{}}},
    storage:{session:{get:async()=>({...state}),set:async v=>{Object.assign(state,v);},remove:async()=>{}}},
    alarms:{create:async()=>{},clear:async()=>{},onAlarm:{addListener:h=>{alarmHandler=h;}}},
    tabs:{query:async()=>[{id:1,url:"https://saveticker.com/news",active:true}],sendMessage:async(_id,m)=>{
      sent.push(m);
      return m.type==="READ_SAVETICKER_NEWS"?{news:{news_list:[]}}:{resource:m.resource,payload:{}};
    }},scripting:{executeScript:async()=>{}}
  };
  const oldFetch=globalThis.fetch;
  globalThis.fetch=async(_url,opts)=>{const m=JSON.parse(opts.body);return {status:200,ok:true,json:async()=>m.resource?{accepted:true}:reply};};
  const code=await readFile(new URL("background.js",import.meta.url),"utf8");
  const settle=async()=>{for(let i=0;i<60;i++)await Promise.resolve();};
  try {
    const module=await import("data:text/javascript;base64,"+Buffer.from(code+"\n//names").toString("base64"));
    assert.deepEqual(module.wantedSymbols({option_symbols:["A","b",null,"BRK.B"]}),["A","BRK.B"]);
    assert.deepEqual(module.wantedSymbols({}),[]);
    alarmHandler({name:"marketlens-saveticker-news"});await settle();
    const asked=sent.filter(m=>m.resource==="options");
    assert.equal(asked.length,1);
    assert.deepEqual(asked[0].symbols,["NVDA","AMD"]);
    // the app names nothing (every candidate already has today's figures): no options read
    sent.length=0;reply={accepted:true,option_symbols:[]};state.supplement.at=Date.now()-600_001;
    alarmHandler({name:"marketlens-saveticker-news"});await settle();
    assert.equal(sent.filter(m=>m.resource==="options").length,0);
    assert.equal(sent.filter(m=>m.resource==="calendar").length,1);
  } finally {globalThis.fetch=oldFetch;delete globalThis.chrome;}
});
