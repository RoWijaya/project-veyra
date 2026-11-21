document.addEventListener("DOMContentLoaded", () => {
  const menu = document.querySelector("[data-business-menu]");
  if (menu) {
    const compact = window.matchMedia("(max-width: 960px)");
    const adapt = () => { menu.open = !compact.matches; };
    compact.addEventListener("change", adapt);
    adapt();
    menu.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !compact.matches || !menu.open) return;
      menu.open = false;
      menu.querySelector("summary").focus();
    });
  }

  const account = document.querySelector("[data-workspace-account]");
  if (account) {
    document.addEventListener("click", (event) => {
      if (account.open && !account.contains(event.target)) account.open = false;
    });
    account.addEventListener("keydown", (event) => {
      if (event.key !== "Escape" || !account.open) return;
      account.open = false;
      account.querySelector("summary").focus();
    });
  }

  const quantityControl = "[data-quantity-stepper]";
  const syncQuantity = (control) => {
    const input = control.querySelector("input[type=number]");
    const minimum = Number(input.min);
    const maximum = Number(input.max);
    const value = Number.isFinite(input.valueAsNumber) ? input.valueAsNumber : minimum;
    control.querySelectorAll("[data-quantity-step]").forEach((button) => {
      button.hidden = false;
      button.disabled = Number(button.dataset.quantityStep) < 0 ? value <= minimum : value >= maximum;
    });
  };
  const syncQuantities = (root) => root.querySelectorAll(quantityControl).forEach(syncQuantity);
  syncQuantities(document);
  document.addEventListener("quantity-controls:refresh", (event) => syncQuantities(event.target));
  document.addEventListener("input", (event) => {
    const control = event.target.closest(quantityControl);
    if (control) syncQuantity(control);
  });
  document.addEventListener("click", (event) => {
    const button = event.target.closest("[data-quantity-step]");
    if (!button) return;
    const control = button.closest(quantityControl);
    const input = control.querySelector("input[type=number]");
    const minimum = Number(input.min);
    const maximum = Number(input.max);
    const value = Number.isFinite(input.valueAsNumber) ? Math.trunc(input.valueAsNumber) : minimum;
    input.value = Math.max(minimum, Math.min(maximum, value + Number(button.dataset.quantityStep)));
    input.dispatchEvent(new Event("input", { bubbles: true }));
    if (button.disabled) input.focus();
  });

  document.querySelectorAll("[data-list-filter]").forEach((input) => {
    const target = document.getElementById(input.dataset.filterTarget);
    if (!target) return;
    const rows = Array.from(target.querySelectorAll("[data-filter-row]"));
    const status = document.querySelector(`[data-filter-status="${CSS.escape(target.id)}"]`);
    const empty = document.createElement("p");
    empty.className = "inline-empty";
    empty.hidden = true;
    empty.textContent = "No matching results. Try another search.";
    target.after(empty);
    input.addEventListener("input", () => {
      const query = input.value.trim().toLocaleLowerCase();
      let visible = 0;
      rows.forEach((row) => {
        row.hidden = !(row.dataset.searchText || row.textContent).toLocaleLowerCase().includes(query);
        if (!row.hidden) visible += 1;
      });
      if (status) status.textContent = `${visible} of ${rows.length} results shown`;
      empty.hidden = visible !== 0 || rows.length === 0;
    });
  });

  const usedIds = new Set();
  document.querySelectorAll(".field input, .field select, .field textarea").forEach((input, index) => {
    const field = input.closest(".field");
    const originalId = input.id;
    if (!input.id || usedIds.has(input.id)) input.id = `${input.id || "field"}-${index}`;
    usedIds.add(input.id);
    if (field.tagName === "LABEL") field.htmlFor = input.id;
    const label = field.querySelector(":scope > label");
    if (label && label.htmlFor === originalId) label.htmlFor = input.id;
    const describedBy = new Set((input.getAttribute("aria-describedby") || "").split(/\s+/).filter(Boolean));
    const notes = field.querySelectorAll("small, .field-error, .field-hint, .errorlist");
    const noteIds = new Set();
    notes.forEach((note, noteIndex) => {
      if (!note.id || noteIds.has(note.id) || originalId !== input.id) note.id = `${input.id}-note-${noteIndex}`;
      noteIds.add(note.id);
      describedBy.add(note.id);
      if (note.matches(".field-error, .errorlist")) input.setAttribute("aria-invalid", "true");
    });
    if (describedBy.size) input.setAttribute("aria-describedby", [...describedBy].join(" "));
  });
  const error = document.querySelector(".form-error[role=alert], .form-error-summary");
  if (error && error.textContent.trim()) {
    error.tabIndex = -1;
    error.focus();
  }

  document.querySelectorAll("[data-password-control]").forEach((control) => {
    const input = control.querySelector("input");
    const button = control.querySelector("[data-password-toggle]");
    const sync = () => {
      const action = input.type === "password" ? "Show" : "Hide";
      button.textContent = action;
      button.setAttribute("aria-label", `${action} ${button.dataset.passwordToggle}`);
      button.setAttribute("aria-controls", input.id);
    };
    button.addEventListener("click", () => {
      input.type = input.type === "password" ? "text" : "password";
      sync();
    });
    sync();
    button.hidden = false;
  });

  const hideMissingImage = (image) => {
    if (image instanceof HTMLImageElement && image.hasAttribute("data-media-image")) image.hidden = true;
  };
  document.addEventListener("error", (event) => hideMissingImage(event.target), true);
  document.querySelectorAll("img[data-media-image]").forEach((image) => {
    if (image.complete && !image.naturalWidth) hideMissingImage(image);
  });

  document.querySelectorAll("[data-media-form]").forEach((form) => {
    const input = form.querySelector('input[type="file"]');
    const preview = form.querySelector("[data-upload-preview]");
    const current = form.querySelector("[data-current-image]");
    input.addEventListener("change", () => {
      const file = input.files[0];
      preview.hidden = true;
      preview.removeAttribute("src");
      if (current) current.hidden = false;
      if (!file || file.size > 5 * 1024 * 1024 || (file.type && !["image/jpeg", "image/png", "image/webp"].includes(file.type))) return;
      const reader = new FileReader();
      reader.addEventListener("load", () => {
        if (input.files[0] === file) preview.src = reader.result;
      });
      reader.readAsDataURL(file);
    });
    preview.addEventListener("load", () => {
      preview.hidden = false;
      if (current) current.hidden = true;
    });
    preview.addEventListener("error", () => {
      preview.hidden = true;
      if (current) current.hidden = false;
    });
  });

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-print-page]")) window.print();
  });
  const pendingForms = new Map();
  document.addEventListener("submit", (event) => {
    if (event.defaultPrevented) return;
    const form = event.target;
    const prompt = form.dataset.confirm || (
      form.hasAttribute("data-confirm-cancel") && form.querySelector("[name=status]")?.value === "cancelled"
        ? "Cancel this order? This action will be recorded in order history." : ""
    );
    if (form.dataset.submitting || (prompt && !window.confirm(prompt))) {
      event.preventDefault();
      return;
    }
    if (form.getAttribute("method")?.toLowerCase() !== "post") return;
    form.dataset.submitting = "true";
    form.setAttribute("aria-busy", "true");
    const submitter = event.submitter;
    if (submitter) submitter.setAttribute("aria-disabled", "true");
    const progress = document.createElement("p");
    progress.className = "form-progress sr-only";
    progress.setAttribute("role", "status");
    form.after(progress);
    const timer = setTimeout(() => {
      progress.classList.remove("sr-only");
      progress.textContent = "Submitting…";
    }, 350);
    pendingForms.set(form, {submitter, progress, timer});
  });
  window.addEventListener("pageshow", () => {
    for (const [form, {submitter, progress, timer}] of pendingForms) {
      clearTimeout(timer);
      progress.remove();
      delete form.dataset.submitting;
      form.removeAttribute("aria-busy");
      submitter?.removeAttribute("aria-disabled");
    }
    pendingForms.clear();
  });

  const station = document.querySelector("[data-station-screen]");
  const orderDesk = document.querySelector("[data-order-stream]");
  if ((station || orderDesk) && window.WebSocket) {
    const queueSelector = station ? "[data-station-queue]" : "[data-order-stream]";
    const queue = document.querySelector(queueSelector);
    const status = station?.querySelector("[data-station-connection]") || document.createElement("p");
    const businessSlug = station?.dataset.stationScreen || orderDesk.dataset.orderStream;
    if (orderDesk) {
      status.className = "field-hint";
      status.setAttribute("role", "status");
      status.textContent = "Connecting to live orders…";
      orderDesk.prepend(status);
    }
    let pending = false;
    let queued = false;
    let stopped = false;
    let socket;
    let timer;
    let delay = 1000;
    let connected = false;
    const retry = document.createElement("button");
    retry.type = "button";
    retry.className = "button button-small";
    retry.textContent = "Retry synchronization";
    retry.hidden = true;
    status.after(retry);
    let queueRequest;
    const stationContent = (section) => JSON.stringify([
      Array.from(section.querySelectorAll(".station-ticket"), (ticket) => [
        ticket.dataset.orderId, ticket.textContent,
      ]),
      section.querySelector(".station-print-jobs")?.textContent || "",
    ]);
    const recover = async () => {
      if (stopped) return;
      if (pending) { queued = true; return; }
      pending = true;
      queueRequest = new AbortController();
      try {
        do {
          queued = false;
          const response = await fetch(window.location.href, {
            cache: "no-store", headers: { Accept: "text/html" }, signal: queueRequest.signal,
          });
          if (!response.ok || response.redirected) throw new Error("Unavailable");
          const snapshot = new DOMParser().parseFromString(await response.text(), "text/html");
          const next = snapshot.querySelector(queueSelector);
          if (!next) throw new Error("No access");
          if (station && stationContent(queue) === stationContent(next)) {
            status.textContent = "Live orders connected · queue synchronized";
            retry.hidden = true;
            continue;
          }
          const active = document.activeElement;
          const priorOrder = queue.contains(active) ? active.closest("[data-order-id]")?.dataset.orderId : null;
          const controlKey = (element) => element.name || element.textContent.trim();
          const orderCard = (parent, id) => parent.querySelector(`[data-order-id="${CSS.escape(id)}"]`);
          // Carry an unfinished tender forward only while payment is still permitted.
          if (orderDesk) {
            for (const details of queue.querySelectorAll("[data-tender-section][open]")) {
              const id = details.closest("[data-order-id]").dataset.orderId;
              const replacement = orderCard(next, id)?.querySelector("[data-tender-section]");
              if (!replacement) continue;
              replacement.open = true;
              for (const field of details.querySelectorAll("input:not([type=hidden]), select")) {
                const target = replacement.querySelector(`[name="${CSS.escape(field.name)}"]`);
                if (target) target.value = field.value;
              }
            }
          }
          const priorAction = priorOrder ? controlKey(active) : null;
          queue.replaceChildren(...(orderDesk ? [status, retry] : []), ...next.childNodes);
          document.dispatchEvent(new CustomEvent("veyra:orders-updated"));
          if (priorOrder) {
            const card = orderCard(queue, priorOrder);
            const replacement = card && Array.from(card.querySelectorAll("button, a, summary, select, input:not([type=hidden])"))
              .find((element) => controlKey(element) === priorAction);
            if (replacement) replacement.focus({ preventScroll: true });
            else { queue.tabIndex = -1; queue.focus({ preventScroll: true }); }
          }
          status.textContent = "Live orders connected · queue synchronized";
          retry.hidden = true;
        } while (queued && !stopped);
      } catch (error) {
        if (stopped || queueRequest.signal.aborted) return;
        status.textContent = "Queue synchronization failed. Orders shown may be out of date.";
        retry.hidden = false;
      } finally { pending = false; }
    };
    retry.addEventListener("click", recover);
    const connect = () => {
      if (stopped) return;
      const protocol = location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${location.host}/ws/business/${encodeURIComponent(businessSlug)}/orders/`);
      socket.addEventListener("open", () => {
        delay = 1000;
        if (connected && orderDesk) document.dispatchEvent(new Event("veyra:catalog-updated"));
        connected = true;
        recover();
      });
      socket.addEventListener("message", (message) => {
        try {
          const update = JSON.parse(message.data);
          if (orderDesk && update.event === "catalog.changed") document.dispatchEvent(new Event("veyra:catalog-updated"));
          // Cash or card tender does not change the kitchen ticket or its actions.
          if (update.order_id && !(station && update.event === "payment_recorded")) recover();
        } catch (error) { /* Ignore malformed frames. */ }
      });
      socket.addEventListener("close", (event) => {
        if (stopped) return;
        if (event.code === 4403) { stopped = true; status.textContent = "Live access ended. Sign in again to continue."; return; }
        status.textContent = "Connection lost · reconnecting to live orders…";
        timer = window.setTimeout(connect, delay);
        delay = Math.min(delay * 2, 20000);
      });
    };
    connect();
    const pauseQueue = () => {
      stopped = true;
      queueRequest?.abort();
      clearTimeout(timer);
      socket?.close();
    };
    document.addEventListener("submit", (event) => {
      if (event.defaultPrevented) return;
      if (queue.contains(event.target) || event.target.matches("[data-pos-cart]")) pauseQueue();
    });
    window.addEventListener("pagehide", pauseQueue);
    window.addEventListener("pageshow", (event) => {
      if (!event.persisted) return;
      stopped = false;
      connect();
    });
  }
});
