
document.addEventListener("DOMContentLoaded", () => {
  const page = document.querySelector("[data-billing-stream]");
  if (!page || !window.WebSocket) return;
  const state = page.querySelector("[data-billing-state]");
  const status = page.querySelector("[data-billing-connection]");
  let socket, timer, request;
  let stopped = false;
  let delay = 1000;
  let queued = false;
  const refresh = async () => {
    if (stopped) return;
    if (request) { queued = true; return; }
    request = new AbortController();
    try {
      const response = await fetch(window.location.href, { cache: "no-store", signal: request.signal });
      if (response.redirected || response.status === 404 || response.status === 403) {
        state.hidden = true;
        stopped = true;
        socket.close();
        status.classList.remove("sr-only");
        status.textContent = "Billing access ended. Sign in to check your access.";
        return;
      }
      if (!response.ok) throw new Error("Billing unavailable");
      const next = new DOMParser().parseFromString(await response.text(), "text/html").querySelector("[data-billing-state]");
      if (!next) throw new Error("Billing unavailable");
      if (state.textContent.trim() !== next.textContent.trim()) {
        const active = document.activeElement;
        const focusKey = active.getAttribute("href") || active.value;
        const hadFocus = state.contains(active);
        state.replaceChildren(...next.childNodes);
        if (hadFocus) {
          const replacement = [...state.querySelectorAll("a, button")].find(control => (control.getAttribute("href") || control.value) === focusKey);
          if (!replacement) state.tabIndex = -1;
          (replacement || state).focus({preventScroll: true});
        }
      }
      status.textContent = "Billing is up to date.";
    } catch (_) {
      if (!stopped && !request.signal.aborted) socket.close();
    } finally {
      request = undefined;
      if (queued) { queued = false; refresh(); }
    }
  };
  function connect() {
    if (stopped) return;
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(protocol + "//" + window.location.host + "/ws/business/" + encodeURIComponent(page.dataset.billingStream) + "/billing/");
    socket.addEventListener("open", () => { delay = 1000; refresh(); });
    socket.addEventListener("message", refresh);
    socket.addEventListener("close", event => {
      if (stopped || event.code === 4403) return;
      status.textContent = "Reconnecting to billing updates.";
      timer = setTimeout(connect, delay);
      delay = Math.min(delay * 2, 20000);
    });
  }
  const pause = () => { stopped = true; request?.abort(); clearTimeout(timer); socket?.close(); };
  window.addEventListener("submit", event => {
    if (!event.defaultPrevented && page.contains(event.target)) pause();
  });
  page.addEventListener("click", event => {
    if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest("a[href]");
    if (!link || link.target || link.hasAttribute("download")) return;
    const target = new URL(link.href);
    if (target.origin === location.origin && (target.pathname !== location.pathname || target.search !== location.search)) pause();
  });
  window.addEventListener("pagehide", pause);
  window.addEventListener("pageshow", event => {
    if (event.persisted) { stopped = false; connect(); }
  });
  connect();
});
