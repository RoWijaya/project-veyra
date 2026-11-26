
document.addEventListener("DOMContentLoaded", () => {
  const editor = document.querySelector("[data-purchase-editor]");
  if (editor) {
    const add = editor.querySelector("[data-add-purchase-line]");
    const template = editor.querySelector("[data-empty-purchase-line]");
    const count = editor.querySelector("[name=lines-TOTAL_FORMS]");
    const syncAdd = () => { add.disabled = Number(count.value) >= 50; };
    add.hidden = false;
    syncAdd();
    add.addEventListener("click", () => {
      if (add.disabled) return;
      const line = template.content.cloneNode(true);
      line.querySelectorAll("[name], [id], label[for]").forEach((element) => {
        ["name", "id", "for"].forEach((attribute) => {
          if (element.hasAttribute(attribute)) {
            element.setAttribute(attribute, element.getAttribute(attribute).replace("__prefix__", count.value));
          }
        });
      });
      add.before(line);
      count.value = Number(count.value) + 1;
      syncAdd();
      add.previousElementSibling.querySelector("select").focus();
    });
  }

  const stream = document.querySelector("[data-purchase-stream]");
  if (!stream) return;
  const receiving = stream.querySelector("[data-receiving-form]");
  const fill = receiving?.querySelector("[data-fill-receiving]");
  if (fill) {
    fill.hidden = false;
    fill.addEventListener("click", () => {
      receiving.querySelectorAll("[data-receiving-line]").forEach((line) => {
        line.querySelector("[name^=accepted_]").value = line.dataset.outstanding;
        line.querySelector("[name^=rejected_]").value = "";
        line.querySelector("[name^=damaged_]").value = "";
      });
    });
  }
  const status = stream.querySelector("[data-purchase-connection]");
  if (!status || !window.WebSocket) return;

  let socket;
  let timer;
  let refreshing = false;
  let stopped = false;
  let refreshAgain = false;
  let delay = 1000;

  const refresh = async () => {
    if (stopped) return;
    if (refreshing) { refreshAgain = true; return; }
    refreshing = true;
    try {
      const response = await fetch(location.href, { cache: "no-store" });
      if (stopped) return;
      const current = stream.querySelector("[data-purchase-refresh]");
      if (response.status === 403 || response.status === 404 || response.redirected) {
        if (current) current.hidden = true;
        if (receiving) receiving.hidden = true;
        status.textContent = "Live access ended. Return to Inventory to check your access.";
        stopped = true;
        socket?.close();
        return;
      }
      if (!response.ok && !(receiving && response.status === 409)) throw new Error("Purchase unavailable");
      const html = await response.text();
      if (stopped) return;
      const page = new DOMParser().parseFromString(html, "text/html");
      if (receiving) {
        const latest = page.querySelector("[data-receiving-form]");
        receiving.querySelector("[data-record-receiving]").disabled = !latest;
        fill.disabled = !latest;
        if (!latest) {
          status.textContent = "This purchase is no longer available for receiving.";
          return;
        }
        receiving.querySelectorAll("[data-receiving-line]").forEach((line) => {
          const incoming = latest.querySelector('[data-receiving-line="' + line.dataset.receivingLine + '"]');
          if (!incoming) return;
          line.dataset.outstanding = incoming.dataset.outstanding;
          for (const selector of ["[data-received-quantity]", "[data-outstanding-label]"]) {
            line.querySelector(selector).textContent = incoming.querySelector(selector).textContent;
          }
        });
      } else {
        const latest = page.querySelector("[data-purchase-refresh]");
        if (!latest) throw new Error("Purchase unavailable");
        if (latest.textContent.trim() !== current.textContent.trim()
            || latest.querySelector("[name=revision]")?.value !== current.querySelector("[name=revision]")?.value) {
          const active = document.activeElement;
          const focusKey = active.getAttribute("href") || active.value;
          const hadFocus = current.contains(active);
          const open = new Set([...current.querySelectorAll("details[open]")].map((item) => item.querySelector("summary").textContent));
          current.replaceChildren(...latest.childNodes);
          current.querySelectorAll("details").forEach((item) => {
            item.open = open.has(item.querySelector("summary").textContent);
          });
          if (hadFocus) {
            const replacement = [...current.querySelectorAll("a, button")].find((item) => (item.getAttribute("href") || item.value) === focusKey);
            if (!replacement) current.tabIndex = -1;
            (replacement || current).focus({ preventScroll: true });
          }
        }
      }
      status.textContent = "Live purchases synchronized.";
    } catch (_) {
      if (stopped) return;
      status.textContent = "Live synchronization failed. Reconnecting…";
      socket?.close();
    } finally {
      refreshing = false;
      if (refreshAgain) { refreshAgain = false; refresh(); }
    }
  };

  const connect = () => {
    if (stopped) return;
    const protocol = location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(protocol + "//" + location.host + "/ws/business/" + encodeURIComponent(stream.dataset.purchaseStream) + "/purchasing/");
    socket.addEventListener("open", () => { delay = 1000; refresh(); });
    socket.addEventListener("message", (message) => {
      let event;
      try { event = JSON.parse(message.data); } catch (_) { return; }
      if (event.event === "purchase.update" && (!stream.dataset.purchaseId || !event.purchase_id || event.purchase_id === stream.dataset.purchaseId)) refresh();
    });
    socket.addEventListener("close", (event) => {
      if (stopped) return;
      if (event.code === 4403) { status.textContent = "Live purchase updates are unavailable."; return; }
      status.textContent = "Reconnecting to live purchases…";
      timer = setTimeout(connect, delay);
      delay = Math.min(delay * 2, 20000);
    });
  };
  const pause = () => {
    stopped = true;
    clearTimeout(timer);
    socket?.close();
  };
  window.addEventListener("submit", (event) => {
    if (!event.defaultPrevented && stream.contains(event.target)) pause();
  });
  window.addEventListener("pagehide", pause);
  window.addEventListener("pageshow", (event) => {
    if (event.persisted) { stopped = false; connect(); }
  });
  connect();
});
