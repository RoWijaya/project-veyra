document.addEventListener("DOMContentLoaded", () => {
  const currency = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 2 });

  document.querySelectorAll("[data-product-picker]").forEach((picker) => {
    let products = Array.from(picker.querySelectorAll("[data-filter-product]"));
    const productList = picker.querySelector("[data-product-results-list]");
    const search = picker.querySelector("[data-product-search]");
    const category = picker.querySelector("[data-product-category]");
    const results = picker.querySelector("[data-product-results]");
    const noResults = picker.querySelector("[data-product-no-results]");
    const selectedLines = picker.querySelector("[data-selection-lines]");
    const emptySelection = picker.querySelector("[data-selection-empty]");
    const clear = picker.querySelector("[data-clear-selection]");
    const submit = picker.querySelector("[data-cart-submit]");
    const useConfigurations = picker.hasAttribute("data-pos-cart");
    const location = picker.querySelector("select[name=location]");
    const draft = picker.querySelector("[data-pos-draft]");
    const cartCount = picker.querySelector("[data-cart-count]");
    const cartTotal = picker.querySelector("[data-cart-total]");
    if ((!products.length && !useConfigurations) || !search || !category) return;

    const voucher = picker.querySelector("[data-pos-voucher]");
    const code = voucher?.querySelector("[name=voucher_code]");
    const token = voucher?.querySelector("[name=voucher_quote_token]");
    const voucherStatus = voucher?.querySelector("[data-pos-voucher-status]");
    const apply = voucher?.querySelector("[value=apply_voucher]");
    const removeVoucher = voucher?.querySelector("[value=remove_voucher]");
    let voucherRequest;
    const pricingInputs = (name) => /^(quantity_|modifiers_|voucher_code$|location$|order_type$|customer$)/.test(name);
    const pricingDraft = () => JSON.stringify([...new FormData(picker)].filter(([name]) => pricingInputs(name)));
    let approvedDraft = token?.value ? pricingDraft() : "";
    const clearApproval = (message) => {
      voucherRequest?.abort();
      voucherRequest = undefined;
      token.value = "";
      approvedDraft = "";
      voucherStatus.textContent = message;
    };

    const productName = (product) => {
      const label = product.querySelector("[data-product-label]");
      return (label?.firstChild?.textContent || "").trim();
    };
    const syncCategories = () => {
      const selected = category.value;
      const names = [...new Set(products.map((product) => product.dataset.productCategoryName))];
      const choices = names.sort((a, b) => a.localeCompare(b)).map((name) => new Option(name, name));
      category.replaceChildren(category.options[0], ...choices);
      category.value = names.includes(selected) ? selected : "";
    };
    syncCategories();

    const updateSelection = () => {
      if (token?.value && approvedDraft !== pricingDraft()) {
        clearApproval("Order changed. Apply the voucher again to review the total.");
      }
      const fragment = document.createDocumentFragment();
      let count = 0;
      let total = 0;
      let invalid = false;
      products.forEach((product) => {
        const configs = useConfigurations ? product.querySelectorAll("[data-pos-config]") : [product];
        configs.forEach((config) => {
          const input = config.querySelector("[data-cart-quantity]");
          const quantity = Math.max(0, Math.min(99, Number.parseInt(input.value, 10) || 0));
          const options = useConfigurations ? orderOptionControls.evaluate(config, location?.value || "") : {
            names: [], surcharge: 0, error: "",
          };
          if (config.dataset.staleOptions) options.error = "An option changed. Reset options or choose an available replacement.";
          const reset = config.querySelector("[data-reset-config]");
          if (reset) reset.hidden = !config.dataset.staleOptions;
          orderOptionControls.showError(config, quantity ? options.error : "");
          if (!quantity) return;
          count += quantity;
          const lineTotal = quantity * (Number(input.dataset.unitPrice || 0) + options.surcharge);
          total += lineTotal;
          invalid ||= !!options.error;
          const item = document.createElement("li");
          const name = document.createElement("span");
          const amount = document.createElement("strong");
          name.textContent = `${quantity} × ${productName(product)}`;
          if (options.names.length) name.textContent += " · " + options.names.join(", ");
          amount.textContent = options.error ? "Review options" : orderOptionControls.money(lineTotal);
          item.append(name, amount);
          fragment.append(item);
        });
      });
      if (useConfigurations) {
        if (cartCount) cartCount.textContent = String(count);
        if (cartTotal) cartTotal.textContent = invalid ? "—" : currency.format(token?.value ? voucher.dataset.total : total);
        if (draft) draft.hidden = !invalid;
      }
      selectedLines?.replaceChildren(fragment);
      if (selectedLines) selectedLines.hidden = count === 0;
      if (emptySelection) emptySelection.hidden = count !== 0;
      if (clear) clear.hidden = count === 0;
      const hasCode = Boolean(code?.value.trim());
      if (submit) submit.disabled = count === 0 || invalid || (hasCode && !token?.value)
        || picker.dataset.orderingUnavailable === "true";
      if (apply) apply.disabled = !hasCode || count === 0 || invalid || Boolean(voucherRequest);
      if (removeVoucher) removeVoucher.hidden = !hasCode;
    };

    if (voucher) {
      picker.addEventListener("input", (event) => {
        if (!pricingInputs(event.target.name)) return;
        if (voucherRequest) clearApproval("Order changed. Apply the voucher again to review the total.");
        updateSelection();
      });
      picker.addEventListener("submit", async (event) => {
        const action = event.submitter?.value;
        if (action === "remove_voucher") {
          event.preventDefault();
          code.value = "";
          clearApproval("Voucher removed.");
          updateSelection();
          code.focus();
          return;
        }
        if (action !== "apply_voucher") return;
        event.preventDefault();
        const data = new FormData(picker);
        data.set("action", "apply_voucher");
        const before = pricingDraft();
        clearApproval("Checking voucher…");
        const request = new AbortController();
        voucherRequest = request;
        updateSelection();
        try {
          const response = await fetch(picker.getAttribute("action") || window.location.href, {
            method: "POST", body: data, headers: { Accept: "application/json" }, signal: request.signal,
          });
          if (!response.headers.get("Content-Type")?.startsWith("application/json")) {
            throw new Error("The voucher could not be checked. Try again.");
          }
          const result = await response.json();
          if (request.signal.aborted) return;
          if (before !== pricingDraft()) throw new Error("Order changed. Apply the voucher again to review the total.");
          if (!response.ok) throw new Error(result.error || "This voucher is unavailable.");
          code.value = result.code;
          token.value = result.token;
          voucher.dataset.total = result.total;
          approvedDraft = pricingDraft();
          voucherStatus.textContent = result.code + " applied · −Rp " + currency.format(result.discount)
            + " · Total Rp " + currency.format(result.total);
        } catch (error) {
          if (!request.signal.aborted) voucherStatus.textContent = error.message;
        } finally {
          if (voucherRequest === request) voucherRequest = undefined;
          updateSelection();
        }
      });
    }
    const resetFilters = picker.querySelector("[data-reset-product-filters]");
    const updateFilter = () => {
      const query = search.value.trim().toLocaleLowerCase();
      let visible = 0;
      products.forEach((product) => {
        const matches = productName(product).toLocaleLowerCase().includes(query)
          && (!category.value || product.dataset.productCategoryName === category.value);
        product.hidden = !matches;
        if (matches) visible += 1;
      });
      if (useConfigurations) {
        picker.querySelector("[data-ordering-controls]").hidden = products.length === 0;
        picker.querySelector("[data-pos-empty]").hidden = products.length !== 0;
        picker.querySelector("[data-pos-summary]").hidden = products.length === 0;
      }
      if (results) {
        results.hidden = products.length === 0;
        results.textContent = `${visible} of ${products.length} ${products.length === 1 ? "product" : "products"} shown. Selected items stay in your order.`;
      }
      if (noResults) noResults.hidden = visible !== 0 || products.length === 0;
      if (resetFilters) resetFilters.hidden = !query && !category.value;
    };
    search.addEventListener("input", updateFilter);
    category.addEventListener("change", updateFilter);
    resetFilters?.addEventListener("click", () => {
      search.value = "";
      category.value = "";
      updateFilter();
      search.focus();
    });
    if (useConfigurations) {
      const configureRow = (row, productId, index, quantity, selected) => {
        row.dataset.configIndex = index;
        const suffix = index === "0" ? "" : "__" + index;
        const input = row.querySelector("[data-cart-quantity]");
        input.name = "quantity_" + productId + suffix;
        input.value = quantity;
        row.querySelectorAll(orderOptionControls.inputs).forEach((choice) => {
          const fieldset = choice.closest("fieldset");
          choice.name = "modifiers_" + productId + suffix + "_" + fieldset.dataset.modifierGroup;
          choice.checked = choice.value ? selected.has(choice.value) : ![...fieldset.querySelectorAll(orderOptionControls.inputs)].some(
            (option) => selected.has(option.value)
          );
        });
        const remove = row.querySelector("[data-remove-config]");
        if (remove) remove.hidden = index === "0";
      };

      let catalogRequest;
      const cancelRequests = () => {
        catalogRequest?.abort();
        voucherRequest?.abort();
      };
      document.addEventListener("submit", (event) => {
        if (!event.defaultPrevented && event.target === picker) cancelRequests();
      });
      window.addEventListener("pagehide", cancelRequests);
      const refreshCatalog = async () => {
        catalogRequest?.abort();
        const request = new AbortController();
        catalogRequest = request;
        const status = picker.querySelector("[data-pos-catalog-status]");
        try {
          const url = new URL(window.location.href);
          const locationId = location?.value || "";
          if (locationId) url.searchParams.set("location", locationId);
          else url.searchParams.delete("location");
          const response = await fetch(url, { signal: request.signal, cache: "no-store" });
          if (!response.ok || response.redirected) throw new Error("Catalog unavailable");
          const documentSnapshot = new DOMParser().parseFromString(await response.text(), "text/html");
          let stale = false;
          const remote = new Map([...documentSnapshot.querySelectorAll("[data-product-id]")]
            .map((product) => [product.dataset.productId, product]));
          const active = document.activeElement;
          const currentProducts = new Map(products.map((product) => [product.dataset.productId, product]));
          let cursor = productList.firstElementChild;
          for (const [id, snapshot] of remote) {
            const product = currentProducts.get(id) || snapshot;
            if (product !== cursor) productList.insertBefore(product, cursor);
            cursor = product.nextElementSibling;
            if (product === snapshot) {
              const add = product.querySelector("[data-add-config]");
              if (add) add.hidden = false;
            }
          }
          let removedSelection = false;
          products.forEach((product) => {
            const snapshot = remote.get(product.dataset.productId);
            if (!snapshot) {
              removedSelection ||= Array.from(product.querySelectorAll("[data-cart-quantity]")).some((input) => Number(input.value) > 0);
              product.remove();
              return;
            }
            const current = product.querySelector("[data-pos-configurations]");
            const incoming = snapshot.querySelector("[data-pos-configurations]");
            const base = incoming.querySelector("[data-pos-config]");
            const choices = (row) => [...row.querySelectorAll("[data-modifier-group]")].map((group) => group.innerHTML + group.dataset.modifierLocations);
            const currentBase = current.querySelector("[data-pos-config]");
            const priorName = productName(product);
            const label = product.querySelector(".pos-product-label");
            const newLabel = snapshot.querySelector(".pos-product-label");
            if (label.innerHTML !== newLabel.innerHTML) label.replaceWith(newLabel);
            product.dataset.productCategoryName = snapshot.dataset.productCategoryName;
            if (JSON.stringify(choices(currentBase)) === JSON.stringify(choices(base))) {
              if (priorName !== productName(product)) {
                current.querySelectorAll("[data-quantity-step], [data-cart-quantity]").forEach((control) => {
                  control.setAttribute("aria-label", control.getAttribute("aria-label").replace(priorName, () => productName(product)));
                });
              }
              current.querySelectorAll("[data-cart-quantity]").forEach((input) => {
                input.dataset.unitPrice = base.querySelector("[data-cart-quantity]").dataset.unitPrice;
              });
              return;
            }
            const addButton = incoming.querySelector("[data-add-config]");
            const section = current.querySelectorAll("[data-pos-config]");
            const next = [...section].map((row) => {
              const checked = new Set([...row.querySelectorAll(orderOptionControls.inputs + ":checked")]
                .filter((choice) => choice.value && !choice.matches(":disabled")).map((choice) => choice.value));
              const replacement = base.cloneNode(true);
              const index = row.dataset.configIndex;
              const quantity = row.querySelector("[data-cart-quantity]").value;
              configureRow(replacement, product.dataset.productId, index, quantity, checked);
              const missing = [...checked].some((id) => ![...replacement.querySelectorAll(orderOptionControls.inputs)]
                .some((option) => option.value === id));
              if (missing || row.dataset.staleOptions) {
                replacement.dataset.staleOptions = "true";
                stale = true;
              }
              const detail = replacement.querySelector("[data-pos-options]");
              if (detail) detail.open = Boolean(row.querySelector("[data-pos-options]")?.open || missing);
              return replacement;
            });
            if (current.contains(document.activeElement)) next[0].querySelector("[data-cart-quantity]").dataset.restoreFocus = "true";
            if (addButton) addButton.hidden = false;
            current.replaceChildren(...next, ...(addButton ? [addButton] : []));
            const focus = current.querySelector("[data-restore-focus]");
            if (focus) {
              delete focus.dataset.restoreFocus;
              focus.focus();
            }
          });
          products = Array.from(productList.children);
          syncCategories();
          updateFilter();
          if (active && active !== document.body && document.activeElement === document.body) {
            (active.isConnected && !active.closest("[hidden]") ? active : search).focus({ preventScroll: true });
          }
          picker.dispatchEvent(new Event("quantity-controls:refresh", { bubbles: true }));
          status.textContent = "Product options and prices are current.";
          if (stale) status.textContent = "Product options changed. Review affected configurations before creating the order.";
          if (removedSelection) status.textContent = "A selected product became unavailable. Review this order before submitting.";
          status.hidden = false;
          updateSelection();
        } catch (error) {
          if (error.name === "AbortError") return;
          status.textContent = "The catalog could not synchronize. Current prices and options will be checked on submission.";
          status.hidden = false;
        }
      };
      picker.addEventListener("input", (event) => {
        if (event.target.matches("[data-modifier-option]")) delete event.target.closest("[data-pos-config]").dataset.staleOptions;
        if (event.target.matches("[data-cart-quantity], [data-modifier-option]")) updateSelection();
      });
      location?.addEventListener("change", () => { updateSelection(); refreshCatalog(); });
      document.addEventListener("veyra:catalog-updated", () => {
        if (token?.value || voucherRequest) {
          clearApproval("Prices or vouchers changed. Apply the voucher again to review the total.");
          updateSelection();
        }
        refreshCatalog();
      });
      picker.querySelectorAll("[data-add-config]").forEach((button) => { button.hidden = false; });
      picker.addEventListener("click", (event) => {
        const add = event.target.closest("[data-add-config]");
        const remove = event.target.closest("[data-remove-config]");
        const reset = event.target.closest("[data-reset-config]");
        if (reset) {
          const row = reset.closest("[data-pos-config]");
          configureRow(row, row.closest("[data-product-id]").dataset.productId, row.dataset.configIndex,
            row.querySelector("[data-cart-quantity]").value, new Set());
          delete row.dataset.staleOptions;
          const detail = row.querySelector("[data-pos-options]");
          if (detail) detail.open = true;
          updateSelection();
          (detail?.querySelector("summary") || row.querySelector("[data-cart-quantity]")).focus();
        }
        if (add) {
          const product = add.closest("[data-product-id]");
          const list = product.querySelector("[data-pos-configurations]");
          const configs = [...list.querySelectorAll("[data-pos-config]")];
          const used = new Set(configs.map((row) => Number(row.dataset.configIndex)));
          let index = 1;
          while (used.has(index)) index += 1;
          if (index > 999) return;
          const row = configs[0].cloneNode(true);
          row.dataset.configIndex = index;
          const quantity = row.querySelector("[data-cart-quantity]");
          configureRow(row, product.dataset.productId, String(index), "1", new Set());
          delete row.dataset.staleOptions;
          row.querySelector("[data-pos-options]").open = true;
          row.querySelector("[data-remove-config]").hidden = false;
          list.insertBefore(row, add);
          picker.dispatchEvent(new Event("quantity-controls:refresh", { bubbles: true }));
          updateSelection();
          quantity.focus();
        }
        if (remove) {
          const list = remove.closest("[data-pos-configurations]");
          remove.closest("[data-pos-config]").remove();
          updateSelection();
          list.querySelector("[data-add-config]").focus();
        }
      });
    } else {
      picker.querySelectorAll("[data-cart-quantity]").forEach((input) => {
        input.addEventListener("input", updateSelection);
      });
    }
    clear?.addEventListener("click", () => {
      const inputs = picker.querySelectorAll("[data-cart-quantity]");
      inputs.forEach((input) => { input.value = "0"; });
      picker.querySelectorAll("[data-pos-config]").forEach((row) => {
        delete row.dataset.staleOptions;
        if (row.dataset.configIndex !== "0") row.remove();
      });
      picker.querySelectorAll(orderOptionControls.inputs).forEach((input) => {
        input.checked = !input.value && input.type === "radio";
      });
      picker.dispatchEvent(new Event("quantity-controls:refresh", { bubbles: true }));
      inputs[0]?.dispatchEvent(new Event("input", { bubbles: true }));
      updateSelection();
    });
    const container = picker.querySelector("[data-product-results-list]");
    if (container && !useConfigurations) new MutationObserver(() => {
      updateFilter();
      updateSelection();
    }).observe(container, { childList: true, subtree: true, attributes: true, attributeFilter: ["data-unit-price"] });
    picker.querySelectorAll("[data-ordering-controls]").forEach((control) => { control.hidden = false; });
    updateFilter();
    updateSelection();
  });

  const initializeOrderCards = () => document.querySelectorAll("[data-order-id]").forEach((card) => {
    if (card.dataset.orderingActionsInitialized === "true") return;
    card.dataset.orderingActionsInitialized = "true";
    const status = card.querySelector("[data-status]");
    const form = card.querySelector("[data-order-status-form]");
    const payment = card.querySelector("[data-payment-status-label]");
    const tender = card.querySelector("[data-tender-section]");
    const options = {
      new: [["preparing", "Start preparing"], ["cancelled", "Cancel order"]],
      preparing: [["ready", "Mark ready"], ["cancelled", "Cancel order"]],
      ready: [["completed", "Complete order"]],
    };
    const updateActions = () => {
      const state = status?.dataset.status;
      if (tender) tender.hidden = ["Paid", "Pending"].includes(payment?.textContent.trim()) || state === "cancelled";
      if (form) {
        form.hidden = !options[state] || (state === "ready" && card.dataset.paymentDue === "true" && payment?.textContent.trim() !== "Paid");
        if (options[state]) {
          const select = form.querySelector("select");
          const chosen = select.value;
          select.replaceChildren(...options[state].map(([value, label]) => new Option(label, value)));
          if (options[state].some(([value]) => value === chosen)) select.value = chosen;
        }
      }
    };
    if (status) new MutationObserver(updateActions).observe(status, { attributes: true, attributeFilter: ["data-status"] });
    if (payment) new MutationObserver(updateActions).observe(payment, { childList: true, subtree: true });
    updateActions();
  });
  initializeOrderCards();
  document.addEventListener("veyra:orders-updated", initializeOrderCards);

  const tracking = document.querySelector("[data-order-tracking]");
  if (tracking) {
    const orderStatus = tracking.querySelector("strong[data-order-status]");
    const paymentStatus = tracking.querySelector("strong[data-payment-status]");
    const descriptions = {
      New: "Your order has been received. Status updates will appear here automatically.",
      Preparing: "The team is preparing your order.",
      Ready: tracking.dataset.fulfillment === "takeaway"
        ? "Your order is ready to collect" + (tracking.dataset.pickupLocation ? " at " + tracking.dataset.pickupLocation : "") + "."
        : "Your order is ready. The team will help with service.",
      Completed: "Your order is complete. Thank you for ordering.",
      Cancelled: "This order was cancelled. Please ask the team if you need help.",
    };
    const qr = tracking.querySelector("[data-guest-payment-pending]");
    const qrExpiry = Date.parse(qr?.dataset.qrExpires || "");
    const updateTracking = () => {
      const state = orderStatus?.textContent.trim();
      const payment = paymentStatus?.textContent.trim();
      const description = tracking.querySelector("[data-tracking-help]");
      if (description && descriptions[state]) description.textContent = descriptions[state];
      const needsReview = tracking.dataset.paymentNeedsReview === "true";
      const review = tracking.querySelector("[data-guest-payment-review]");
      if (review) review.hidden = !needsReview;
      const qrAvailable = qrExpiry > Date.now();
      if (qr) {
        qr.hidden = needsReview || payment !== "Pending" || state === "Cancelled";
        qr.querySelectorAll(".payment-qr, [data-qr-expiry]").forEach((node) => { node.hidden = !qrAvailable; });
      }
      const retry = tracking.querySelector("[data-guest-payment-retry]");
      if (retry) {
        retry.hidden = needsReview || (payment === "Pending" && qrAvailable) || payment === "Paid" || state === "Cancelled" || state === "Completed";
        retry.querySelector("button").textContent = payment === "Pending" ? "Check QRIS status" : "Try QRIS again";
      }
    };
    [orderStatus, paymentStatus].filter(Boolean).forEach((node) => {
      new MutationObserver(updateTracking).observe(node, { childList: true, subtree: true });
    });
    updateTracking();
    if (qrExpiry > Date.now()) window.setTimeout(updateTracking, qrExpiry - Date.now() + 20);
  }
});
