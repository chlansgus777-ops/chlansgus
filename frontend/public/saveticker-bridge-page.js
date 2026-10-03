(() => {
  const token = new URLSearchParams(location.hash.slice(1)).get("key");
  const button = document.getElementById("pair");
  const status = document.getElementById("status");
  const origin = location.origin;
  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== origin) return;
    if (event.data?.type === "MARKETLENS_BRIDGE_PRESENT") {
      button.disabled = !/^[a-f0-9]{64}$/.test(token ?? "");
      status.textContent = button.disabled ? "연결 주소가 유효하지 않습니다. MarketLens 설정에서 새 연결을 시작하세요." : "확장 확인 완료 · 연결 버튼을 눌러주세요.";
    }
    if (event.data?.type === "MARKETLENS_BRIDGE_PAIRED") {
      status.textContent = event.data.ok ? "브라우저 연결을 등록했습니다. 수집 성공 여부는 MarketLens 설정에서 확인합니다." : "브라우저 등록 실패. MarketLens에서 새 연결을 시작하세요.";
      button.disabled = event.data.ok;
      if (event.data.ok) history.replaceState(null, "", location.pathname); // don't leave a delegated key in the address
    }
  });
  button.addEventListener("click", () => { button.disabled = true; status.textContent = "브라우저 연결 등록 중…"; window.postMessage({type: "MARKETLENS_BRIDGE_PAIR", key: token}, origin); });
  window.postMessage({type: "MARKETLENS_BRIDGE_PING"}, origin);
  setTimeout(() => { if (button.disabled && status.textContent.includes("확인하는 중")) status.textContent = "확장이 연결되지 않았습니다. Chrome·Edge에 확장을 설치하고 이 페이지를 새로고침하세요."; }, 1500);
})();
