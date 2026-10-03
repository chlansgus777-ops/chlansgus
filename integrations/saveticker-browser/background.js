const ALARM = "marketlens-saveticker-news";
let running = false;

export function validPair(endpoint, key) {
  if (typeof endpoint !== "string" || !/^http:\/\/(127\.0\.0\.1|localhost)(:\d{1,5})?\/api\/saveticker\/browser\/news$/.test(endpoint)
    || typeof key !== "string" || !/^[a-f0-9]{64}$/.test(key)) return false;
  try { return Number(new URL(endpoint).port || "80") <= 65535; } catch { return false; }
}

async function deliver(pair, message, canReportFormat=true) {
  const cancel = new AbortController();
  const timer = setTimeout(() => cancel.abort(), 8_000);
  try {
    const r = await fetch(pair.endpoint, {method: "POST", headers: {
      "Content-Type": "application/json", "X-MarketLens-News-Bridge": pair.key,
    }, body: JSON.stringify(message), signal: cancel.signal});
    if (r.status === 401) { // expired, revoked, or app restarted: stop rather than silently pairing elsewhere
      await chrome.storage.session.remove(["pair","supplement"]);
      await chrome.alarms.clear(ALARM);
    }
    if (canReportFormat && [413,422].includes(r.status)) {
      await deliver(pair,message.resource?{resource:message.resource,error:"FORMAT"}:{error:"FORMAT"},false);
    }
  } finally { clearTimeout(timer); }
}

async function collect() {
  if (running) return;
  running = true;
  try {
    const {pair,supplement} = await chrome.storage.session.get(["pair","supplement"]);
    if (!pair || !validPair(pair.endpoint, pair.key)) return;
    const tabs = await chrome.tabs.query({url: "https://saveticker.com/*"});
    if (!tabs.length) { await deliver(pair, {error: "NO_TAB"}); return; }
    const tab = tabs.find((t) => t.active) ?? tabs[0];
    let result;
    try {
      result = await chrome.tabs.sendMessage(tab.id, {type: "READ_SAVETICKER_NEWS"});
    } catch { // the source tab may have been open before installing the extension
      await chrome.scripting.executeScript({target: {tabId: tab.id}, files: ["source.js"]});
      result = await chrome.tabs.sendMessage(tab.id, {type: "READ_SAVETICKER_NEWS"});
    }
    if (!result || !("news" in result || "error" in result)) result = {error: "FORMAT"};
    // A newer pairing supersedes this read: never send an old browser result into a new app connection.
    const latest = (await chrome.storage.session.get("pair")).pair;
    if (latest?.key !== pair.key) return;
    await deliver(pair, result);
    if ((await chrome.storage.session.get("pair")).pair?.key!==pair.key) return;
    const lastSupplement=supplement?.key===pair.key&&supplement.endpoint===pair.endpoint&&Number.isFinite(supplement.at)&&supplement.at<=Date.now()?supplement.at:0;
    if (result.news && Date.now()-lastSupplement>10*60_000) {
      // MV3 workers are suspended between alarms. Persist the throttle with this pairing,
      // so a cold worker cannot repeat every supplemental request at the news cadence.
      await chrome.storage.session.set({supplement:{key:pair.key,endpoint:pair.endpoint,at:Date.now()}});
      for (const resource of ["calendar","details","reports","options"]) {
        const extra=await chrome.tabs.sendMessage(tab.id,{type:"READ_SAVETICKER_EXTRA",resource});
        const current=(await chrome.storage.session.get("pair")).pair;
        if (current?.key!==pair.key) return;
        if (extra?.resource===resource) await deliver(pair,extra);
      }
    }
  } catch { /* keep the last valid app data; next alarm retries a transient disconnect */ }
  finally { running = false; }
}

chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id) return;
  if (message?.type === "PAIR") {
    // Only the localhost pairing page can enroll. A SaveTicker page cannot choose an export destination.
    if (!sender.tab?.url || !/^http:\/\/(127\.0\.0\.1|localhost)(:\d{1,5})?\/saveticker-bridge\.html(?:[#?]|$)/.test(sender.tab.url)
        || !validPair(message.endpoint, message.key) || new URL(sender.tab.url).origin !== new URL(message.endpoint).origin) {
      respond({ok: false}); return;
    }
    (async () => {
      await chrome.storage.session.set({pair: {endpoint: message.endpoint, key: message.key}});
      await chrome.alarms.create(ALARM, {periodInMinutes: 2});
      respond({ok: true});
      const tabs = await chrome.tabs.query({url: "https://saveticker.com/*"});
      if (!tabs.length) await chrome.tabs.create({url: "https://saveticker.com/news"});
      await collect();
    })().catch(() => respond({ok: false}));
    return true;
  }
  if (message?.type === "SAVETICKER_READY" && sender.tab?.url?.startsWith("https://saveticker.com/")) void collect();
});
chrome.alarms.onAlarm.addListener((alarm) => { if (alarm.name === ALARM) void collect(); });
