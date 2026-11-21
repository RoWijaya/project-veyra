document.addEventListener("DOMContentLoaded", () => {
  const receipt = document.querySelector("[data-receipt-stream]");
  if (!receipt) return;
  receipt.querySelector("[data-print-receipt]")?.addEventListener("click", () => window.print());
  const connection = receipt.querySelector("[data-receipt-connection]");
  const retry = document.createElement("button");
  retry.type = "button";
  retry.className = "button button-light";
  retry.textContent = "Retry synchronization";
  retry.hidden = true;
  connection.after(retry);
  retry.addEventListener("click", synchronize);
  let socket;
  let timer;
  let stopped = false;
  let delay = 1000;
  let refreshing = false;
  let repeat = false;
  let request;

  async function synchronize() {
    if (stopped) return;
    if (refreshing) { repeat = true; return; }
    refreshing = true;
    request = new AbortController();
    try {
      const response = await fetch(window.location.href, {cache:"no-store", signal:request.signal});
      if (!response.ok || response.redirected) throw new Error("Receipt unavailable");
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const replacement = page.querySelector("#receipt-print-state");
      if (!replacement) throw new Error("Receipt unavailable");
      const current = receipt.querySelector("#receipt-print-state");
      const selected = current.querySelector("select")?.value;
      const focused = current.contains(document.activeElement);
      current.replaceChildren(...replacement.childNodes);
      const select = current.querySelector("select");
      if (select && Array.from(select.options).some(option => option.value === selected)) select.value = selected;
      if (focused) {
        current.tabIndex = -1;
        current.focus({preventScroll:true});
      }
      connection.textContent = "Print status is live.";
      retry.hidden = true;
    } catch {
      if (stopped || request.signal.aborted) return;
      connection.textContent = "Print status could not synchronize. Check your connection.";
      retry.hidden = false;
    } finally {
      refreshing = false;
      if (repeat && !stopped) { repeat = false; synchronize(); }
    }
  }
  function connect() {
    if (stopped) return;
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${protocol}://${location.host}/ws/business/${encodeURIComponent(receipt.dataset.receiptStream)}/orders/`);
    socket.onopen = () => { delay = 1000; synchronize(); };
    socket.onmessage = event => {
      let data;
      try { data = JSON.parse(event.data); } catch { return; }
      if (data.order_id === receipt.dataset.receiptOrder) synchronize();
    };
    socket.onclose = event => {
      if (stopped) return;
      if (event.code === 4403) {
        stopped = true;
        connection.textContent = "Live access ended. Sign in again to see current print status.";
        return;
      }
      connection.textContent = "Print updates disconnected. Reconnecting…";
      timer = setTimeout(connect, delay);
      delay = Math.min(delay * 2, 30000);
    };
  }
  const pause = () => {
    stopped = true;
    request?.abort();
    clearTimeout(timer);
    socket?.close();
  };
  receipt.addEventListener("submit", (event) => {
    if (!event.defaultPrevented) pause();
  });
  window.addEventListener("pagehide", pause);
  window.addEventListener("pageshow", event => { if (event.persisted) { stopped = false; connect(); } });
  connect();
});
