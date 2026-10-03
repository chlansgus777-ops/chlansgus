// Pairing only on MarketLens's dedicated, secret-free static page. The page contains a NEWS-only delegated key.
if (location.pathname === "/saveticker-bridge.html") {
  const ownOrigin = location.origin;
  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== ownOrigin) return;
    if (event.data?.type === "MARKETLENS_BRIDGE_PING") {
      window.postMessage({type: "MARKETLENS_BRIDGE_PRESENT"}, ownOrigin);
    }
    if (event.data?.type === "MARKETLENS_BRIDGE_PAIR") {
      chrome.runtime.sendMessage({type: "PAIR", endpoint: ownOrigin + "/api/saveticker/browser/news", key: event.data.key})
        .then((result) => window.postMessage({type: "MARKETLENS_BRIDGE_PAIRED", ok: result?.ok === true}, ownOrigin))
        .catch(() => window.postMessage({type: "MARKETLENS_BRIDGE_PAIRED", ok: false}, ownOrigin));
    }
  });
  window.postMessage({type: "MARKETLENS_BRIDGE_PRESENT"}, ownOrigin);
}
