document.addEventListener("DOMContentLoaded", () => {
  const store = document.querySelector("[data-customer-store]");
  if (!store) return;
  const search = store.querySelector("[data-menu-search]");
  let category = "";
  const connection = store.querySelector("[data-store-connection]");
  const feedback = store.querySelector("[data-cart-feedback]");
  let promotion = "";
  let targetProduct = "";

  const filter = () => {
    const products = [...store.querySelectorAll("[data-menu-product]")];
    const query = (search?.value || "").trim().toLocaleLowerCase();
    const categories = [...store.querySelectorAll("[data-menu-category]")];
    if (!categories.some((button) => button.dataset.menuCategory === category)) category = "";
    categories.forEach((button) => button.setAttribute("aria-pressed", String(button.dataset.menuCategory === category)));
    let count = 0;
    products.forEach((product) => {
      const matches = product.dataset.name.toLocaleLowerCase().includes(query)
        && (!category || product.dataset.category === category)
        && (!targetProduct || product.dataset.menuProduct === targetProduct)
        && (!promotion || product.dataset.promotions.split(" ").includes(promotion));
      product.hidden = !matches;
      if (matches) count += 1;
    });
    const empty = store.querySelector("[data-menu-empty]");
    if (empty) empty.hidden = count > 0 || products.length === 0;
    const status = store.querySelector("[data-menu-count]");
    if (status) status.textContent = `${count} menu ${count === 1 ? "item" : "items"}`;
  };
  const reset = () => {
    search.value = "";
    category = "";
    promotion = "";
    targetProduct = "";
  };
  if (search) {
    store.querySelector("[data-store-filters]").hidden = false;
    search.addEventListener("input", filter);
    filter();
  }

  store.addEventListener("click", (event) => {
    const categoryButton = event.target.closest("[data-menu-category]");
    if (categoryButton) {
      category = categoryButton.dataset.menuCategory;
      promotion = "";
      targetProduct = "";
      filter();
      return;
    }
    const banner = event.target.closest("[data-banner-target]");
    if (!banner || !search) return;
    reset();
    if (banner.dataset.bannerType === "product") targetProduct = banner.dataset.bannerTarget;
    if (banner.dataset.bannerType === "category") category = banner.dataset.bannerTarget;
    if (banner.dataset.bannerType === "promotion") promotion = banner.dataset.bannerTarget;
    filter();
  });

  if (store.hasAttribute("data-render-preview")) {
    // The private editor reports errors without navigating or connecting to a guest stream.
    return;
  }

  let adding = false;
  store.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-cart-add]");
    if (!form || event.defaultPrevented) return;
    event.preventDefault();
    if (adding) return;
    adding = true;
    const button = event.submitter;
    button.disabled = true;
    feedback.textContent = "Adding to your order…";
    try {
      const response = await fetch(form.getAttribute("action"), {
        method: "POST", body: new FormData(form), credentials: "same-origin",
        headers: { Accept: "application/json" },
      });
      const data = await response.json();
      store.querySelectorAll("input[name=revision]").forEach((input) => { input.value = data.revision; });
      if (!response.ok) throw new Error(data.error || "The order could not be updated. Try again.");
      store.querySelectorAll("[data-cart-count]").forEach((count) => { count.textContent = data.count; });
      feedback.textContent = "Added to your order. Open Your order to review and checkout.";
    } catch (error) {
      feedback.textContent = error.message || "Connection lost. Check Your order before trying again.";
    } finally {
      adding = false;
      button.disabled = false;
      form.removeAttribute("aria-busy");
      delete form.dataset.submitting;
      button.removeAttribute("aria-disabled");
    }
  });

  let updatingQuantity = false;
  store.addEventListener("submit", async (event) => {
    const form = event.target.closest("[data-cart-stepper]");
    const button = event.submitter;
    if (!form || !["increment", "decrement"].includes(button?.value)) {
      if (updatingQuantity && event.target.closest("#store-order-layout")) event.preventDefault();
      return;
    }
    event.preventDefault();
    if (updatingQuantity) return;

    const feedback = store.querySelector("[data-quantity-feedback]");
    const data = new FormData(form);
    data.set("action", button.value);
    updatingQuantity = true;
    form.setAttribute("aria-busy", "true");
    feedback.className = "sr-only";
    try {
      const response = await fetch(window.location.href, {
        method: "POST", body: data, credentials: "same-origin",
        headers: { "X-Cart-Update": "quantity" },
      });
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      if (!page.querySelector("#store-order-lines")) {
        if (page.querySelector(".store-empty")) window.location.reload();
        else throw new Error("We could not update your order. Try again.");
        return;
      }
      const restoreFocus = form.contains(document.activeElement);
      const oldVoucher = store.querySelector("#store-voucher");
      const voucherInput = oldVoucher.querySelector("[name=voucher_code]");
      const draftCode = voucherInput?.value;
      ["store-order-lines", "store-totals", "store-unavailable", "store-voucher"].forEach((id) => {
        document.getElementById(id).replaceWith(page.getElementById(id));
      });
      const nextVoucherInput = store.querySelector("#store-voucher [name=voucher_code]");
      if (nextVoucherInput && draftCode !== undefined && draftCode !== oldVoucher.dataset.applied) {
        nextVoucherInput.value = draftCode;
      }
      ["revision", "quote"].forEach((name) => {
        const latest = page.querySelector(".store-checkout [name=" + name + "]");
        store.querySelectorAll("[name=" + name + "]").forEach((field) => { field.value = latest.value; });
      });
      const checkout = "button[value=checkout]";
      store.querySelector(checkout).disabled = page.querySelector(checkout).disabled;
      store.querySelectorAll("[data-cart-count]").forEach((count) => {
        count.textContent = page.querySelector("[data-cart-count]").textContent;
      });
      if (restoreFocus) {
        const replacement = store.querySelector('[data-cart-stepper="' + form.dataset.cartStepper + '"]');
        const sameButton = replacement?.querySelector('button[value="' + button.value + '"]:enabled');
        (sameButton || replacement?.querySelector("button:enabled") || store.querySelector(checkout)).focus();
      }
      const error = page.querySelector(".store-main > .form-error[role=alert]");
      if (error) feedback.className = "form-error";
      feedback.textContent = error?.textContent || "Quantity updated. " + store.querySelector(".store-total").textContent;
    } catch (error) {
      feedback.className = "form-error";
      feedback.textContent = error.message || "We could not update your order. Try again.";
    } finally {
      updatingQuantity = false;
      form.removeAttribute("aria-busy");
    }
  });

  if (!window.WebSocket) {
    connection.textContent = "Live updates are unavailable in this browser. Prices are confirmed at checkout.";
    return;
  }
  let socket;
  let stopped = false;
  let timer;
  let retryDelay = 1000;
  let syncing = false;
  let pending = false;
  let menuRequest;
  const menuUrl = window.location.href;
  const pauseMenuUpdates = () => {
    stopped = true;
    menuRequest?.abort();
    clearTimeout(timer);
    if (socket?.readyState === WebSocket.OPEN) socket.close();
  };
  const retireLink = () => {
    stopped = true;
    clearTimeout(timer);
    socket?.close();
    connection.textContent = "This menu link is no longer available. Ask the team for a current link.";
    store.querySelectorAll("[data-cart-add] button:not([type=button]), button[value=checkout]")
      .forEach((button) => { button.disabled = true; });
  };
  const refresh = async () => {
    if (stopped) return;
    if (syncing) { pending = true; return; }
    syncing = true;
    menuRequest = new AbortController();
    try {
      const response = await fetch(menuUrl, {
        credentials: "same-origin", cache: "no-store", signal: menuRequest.signal,
        headers: { "X-Storefront-Refresh": "1" },
      });
      if ([403, 404].includes(response.status)) {
        retireLink();
        return;
      }
      if (!response.ok) throw new Error("Menu unavailable");
      const page = new DOMParser().parseFromString(await response.text(), "text/html");
      const expanded = [...store.querySelectorAll(".store-product-detail[open]")].map((details) => ({
        id: details.closest("[data-menu-product]").id,
        quantity: details.querySelector("input[name=quantity]").value,
        options: new Set([...details.querySelectorAll("[data-modifier-option]:checked")].map((input) => input.value).filter(Boolean)),
      }));
      const focusedCategory = document.activeElement?.dataset.menuCategory;
      const categoryKeys = (root) => [...root.querySelectorAll("[data-menu-category]")]
        .map((button) => [button.dataset.menuCategory, button.textContent]);
      if (JSON.stringify(categoryKeys(store)) !== JSON.stringify(categoryKeys(page))) {
        document.getElementById("store-categories")?.replaceWith(page.getElementById("store-categories"));
        if (focusedCategory !== undefined) {
          const buttons = [...store.querySelectorAll("[data-menu-category]")];
          (buttons.find((button) => button.dataset.menuCategory === focusedCategory) || buttons[0])?.focus();
        }
      }
      const freshStore = page.querySelector("[data-customer-store]");
      const theme = document.querySelector("link#store-design");
      const nextTheme = page.querySelector("link#store-design")?.getAttribute("href");
      ["orderingCustomized", "menuLayout", "menuButton", "menuCard"].forEach((key) => {
        if (freshStore?.dataset[key] !== undefined) store.dataset[key] = freshStore.dataset[key];
      });
      if (theme && nextTheme && theme.getAttribute("href") !== nextTheme) theme.href = nextTheme;
      const themeColor = document.querySelector('meta[name="theme-color"]');
      const freshColor = page.querySelector('meta[name="theme-color"]');
      if (themeColor && freshColor) themeColor.content = freshColor.content;
      const brand = document.getElementById("store-brand");
      const latestBrand = page.getElementById("store-brand");
      if (brand && latestBrand && brand.innerHTML !== latestBrand.innerHTML) {
        brand.replaceChildren(...latestBrand.childNodes);
        document.title = page.title;
      }
      const intro = document.getElementById("store-intro");
      const latestIntro = page.getElementById("store-intro");
      if (intro && latestIntro && intro.innerHTML !== latestIntro.innerHTML) intro.replaceWith(latestIntro);
      ["store-products", "store-banners", "store-information"].forEach((id) => {
        const existing = document.getElementById(id);
        const replacement = page.getElementById(id);
        if (existing && replacement) existing.replaceWith(replacement);
      });
      expanded.forEach(({ id, quantity, options }) => {
        const details = document.getElementById(id)?.querySelector("details");
        if (!details) return;
        details.open = true;
        details.querySelector("input[name=quantity]").value = quantity;
        details.querySelectorAll("[data-modifier-option]").forEach((input) => {
          if (input.value) input.checked = options.has(input.value);
        });
      });
      store.dispatchEvent(new Event("modifiers:refresh", { bubbles: true }));
      store.dispatchEvent(new Event("quantity-controls:refresh", { bubbles: true }));
      filter();
      const currentLines = store.querySelectorAll("[data-cart-line]");
      if (currentLines.length) {
        const latestLines = new Map(Array.from(page.querySelectorAll("[data-cart-line]"), (line) => [
          line.dataset.cartLine, line.querySelector(".store-cart-image"),
        ]));
        for (const line of currentLines) {
          const image = line.querySelector(".store-cart-image");
          const fresh = latestLines.get(line.dataset.cartLine);
          if (!fresh) image?.remove();
          else if (!image) line.prepend(fresh);
          else if (image.getAttribute("src") !== fresh.getAttribute("src")) image.replaceWith(fresh);
        }
      }
      const originalQuote = store.querySelector("[name=quote]");
      const currentTotals = store.querySelector("#store-totals");
      const latestTotals = page.querySelector("#store-totals");
      const lineText = (page) => [...page.querySelectorAll(".store-cart-item")].map((line) => line.textContent).join("|");
      const voucherText = (page) => page.querySelector("#store-voucher")?.textContent.trim();
      if (originalQuote && (currentTotals?.textContent !== latestTotals?.textContent
        || lineText(store) !== lineText(page) || voucherText(store) !== voucherText(page))) {
        connection.textContent = "Menu prices, options, or voucher eligibility changed. Review your order again before checkout.";
      } else {
        connection.textContent = "Menu synchronized. Prices are confirmed at checkout.";
      }
    } catch (_) {
      if (stopped || menuRequest.signal.aborted) return;
      connection.textContent = "Menu synchronization failed. Reconnecting…";
      socket?.close();
    } finally {
      syncing = false;
      if (pending) { pending = false; refresh(); }
    }
  };
  const connect = () => {
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${window.location.host}/ws/guest/menu/${encodeURIComponent(store.dataset.menuToken)}/`);
    socket.addEventListener("open", () => {
      if (stopped) return;
      retryDelay = 1000;
      connection.textContent = "Menu updates connected";
      refresh();
    });
    socket.addEventListener("message", refresh);
    socket.addEventListener("close", (event) => {
      if (stopped) return;
      if ([4401, 4403, 4404].includes(event.code)) {
        retireLink();
        return;
      }
      connection.textContent = "Connection interrupted. Reconnecting to menu updates…";
      timer = window.setTimeout(connect, retryDelay);
      retryDelay = Math.min(retryDelay * 2, 30000);
    });
  };
  document.addEventListener("submit", (event) => {
    if (!event.defaultPrevented && store.contains(event.target)) pauseMenuUpdates();
  });
  window.addEventListener("pagehide", pauseMenuUpdates);
  window.addEventListener("pageshow", (event) => {
    if (!event.persisted) return;
    stopped = false;
    connect();
  });
  connect();
});
