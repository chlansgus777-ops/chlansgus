(() => {
  const status = document.getElementById('status');
  const progress = document.getElementById('progress');
  const retry = document.getElementById('retry');
  let generation = 0;
  let timer;
  let request;
  const fail = (message) => {
    generation++;
    clearTimeout(timer);
    request?.abort();
    status.textContent = message;
    progress.hidden = true;
    retry.hidden = false;
  };
  window.marketlensStartupFailure = fail;
  const start = () => {
    const current = ++generation;
    const deadline = Date.now() + 30000;
    status.textContent = '저장된 설정과 데이터를 준비하고 있습니다.';
    retry.hidden = true;
    progress.hidden = false;
    const check = async () => {
      if (current !== generation) return;
      request = new AbortController();
      const timeout = setTimeout(() => request.abort(), 1500);
      try {
        const response = await fetch(`${window.__MARKETLENS_API__}/api/health/ready`, { signal: request.signal, cache: 'no-store' });
        const result = response.ok && await response.json();
        if (current !== generation) return;
        if (result?.ready === true) {
          location.replace('index.html');
          return;
        }
      } catch { /* Extraction and database startup can take a few seconds. */ }
      finally { clearTimeout(timeout); }
      if (current !== generation) return;
      if (Date.now() >= deadline) {
        fail('앱 서버가 아직 준비되지 않았습니다. 다시 확인하거나 앱을 닫고 재실행해주세요. 계속 실패하면 데이터 폴더의 logs를 확인해주세요.');
      } else {
        timer = setTimeout(check, 250);
      }
    };
    void check();
  };
  retry.addEventListener('click', start);
  window.addEventListener('pagehide', () => { generation++; clearTimeout(timer); request?.abort(); });
  start();
})();
