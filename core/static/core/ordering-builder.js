document.addEventListener("DOMContentLoaded", () => {
  const editor = document.querySelector("[data-ordering-builder]");
  if (!editor) return;
  const form = editor.querySelector("#ordering-builder-form");
  const viewport = editor.querySelector("[data-builder-viewport]");
  const frame = editor.querySelector("[data-builder-preview]");
  const preview = editor.querySelector("[data-preview-submit]");
  const status = editor.querySelector("[data-preview-status]");
  const dirty = editor.querySelector("[data-builder-dirty]");
  const controls = [...editor.querySelectorAll("[data-preview-width]")];
  let width = viewport.clientWidth < 480 ? 390 : 1120;
  let timer;
  let saving = false;
  let previewVersion = 0;

  const warnAboutUnsavedChanges = (event) => {
    if (saving) return;
    event.preventDefault();
    event.returnValue = true;
  };
  if (!dirty.hidden) window.addEventListener("beforeunload", warnAboutUnsavedChanges);
  window.addEventListener("pageshow", () => { saving = false; });

  const sizePreview = () => {
    const scale = Math.min(1, viewport.clientWidth / width);
    frame.style.width = width + "px";
    frame.style.height = viewport.clientHeight / (scale || 1) + "px";
    frame.style.transform = "scale(" + scale + ")";
    controls.forEach((button) => button.setAttribute("aria-pressed", String(Number(button.dataset.previewWidth) === width)));
  };
  new ResizeObserver(sizePreview).observe(viewport);
  sizePreview();
  controls.forEach((button) => button.addEventListener("click", () => {
    width = Number(button.dataset.previewWidth);
    sizePreview();
  }));

  const updatePreviewStatus = () => {
    const contents = frame.contentDocument;
    const rendered = contents?.querySelector("[data-render-preview]");
    // A previous response can finish while a newer edit is still waiting to submit.
    if (rendered && previewVersion && rendered.dataset.previewRequest !== String(previewVersion)) return;
    const errors = contents?.querySelectorAll("[data-preview-errors] > li");
    if (errors?.length) {
      const first = errors[0].textContent.trim();
      status.textContent = `Preview shows the saved design. ${first}${errors.length > 1 ? " See the preview for other errors." : ""}`;
    } else if (rendered) {
      status.textContent = dirty.hidden ? "Showing the saved design. Ordering is disabled here." : "Preview up to date. Unsaved changes are private.";
    } else {
      status.textContent = "Preview unavailable. Reload the editor to try again.";
    }
  };
  frame.addEventListener("load", updatePreviewStatus);
  if (frame.contentDocument?.querySelector("[data-render-preview]")) updatePreviewStatus();

  const requestPreview = () => {
    clearTimeout(timer);
    if (!preview) return;
    previewVersion += 1;
    timer = setTimeout(() => form.requestSubmit(preview), 420);
    status.textContent = "Updating preview…";
  };
  const change = () => {
    dirty.hidden = false;
    dirty.textContent = "Unsaved changes";
    window.addEventListener("beforeunload", warnAboutUnsavedChanges);
    requestPreview();
  };
  form.addEventListener("input", (event) => {
    if (!preview || event.target.matches("[data-builder-search], [data-color-preset]")) return;
    change();
  });
  form.addEventListener("change", (event) => {
    if (preview && event.target.matches('input[type="file"], input[type="color"]')) change();
  });
  form.addEventListener("submit", (event) => {
    clearTimeout(timer);
    if (event.submitter === preview) {
      form.elements.preview_request.value = String(++previewVersion);
      status.textContent = "Updating preview…";
      return;
    }
    saving = true;
    queueMicrotask(() => { if (event.defaultPrevented) saving = false; });
  });

  const location = editor.querySelector("[data-preview-location]");
  const initialUrl = new URL(frame.src);
  location?.addEventListener("change", () => {
    if (preview) {
      requestPreview();
    } else {
      initialUrl.searchParams.set("location", location.value);
      initialUrl.searchParams.set("preview_request", String(++previewVersion));
      status.textContent = "Updating preview…";
      frame.src = initialUrl.toString();
    }
  });

  const colorInputs = [...form.querySelectorAll('.builder-colors input[type="color"]')];
  const updateColorValues = () => {
    colorInputs.forEach((input) => {
      const value = input.parentElement.querySelector("output");
      if (value) value.value = input.value.toUpperCase();
    });
  };
  updateColorValues();
  const palette = form.querySelector("[data-color-preset]");
  if (palette && preview) {
    palette.closest("[data-preset-control]").hidden = false;
    const presets = JSON.parse(document.getElementById("ordering-color-presets").textContent);
    palette.addEventListener("change", () => {
      if (!presets[palette.value]) return;
      for (const [key, color] of Object.entries(presets[palette.value])) form.elements[key].value = color;
      updateColorValues();
      change();
    });
    const syncPreset = () => {
      updateColorValues();
      const match = Object.entries(presets).find(([, colors]) => Object.entries(colors).every(
        ([key, color]) => form.elements[key].value.toLowerCase() === color.toLowerCase(),
      ));
      palette.value = match?.[0] || "";
    };
    syncPreset();
    colorInputs.forEach((input) => {
      input.addEventListener("input", syncPreset);
      input.addEventListener("change", syncPreset);
    });
  }

  if (preview) for (const list of editor.querySelectorAll("[data-builder-list]")) {
    const rows = list.querySelector("[data-builder-rows]");
    if (!rows) continue;
    const rowList = () => [...rows.children];
    const hint = list.querySelector("[data-builder-list-status]");
    const search = list.querySelector("[data-builder-search]");
    search.closest("[data-builder-search-field]").hidden = false;
    const updateMoves = () => {
      const items = rowList().filter((row) => !row.hidden);
      items.forEach((row, index) => {
        row.querySelector('[data-move="-1"]').disabled = index === 0;
        row.querySelector('[data-move="1"]').disabled = index === items.length - 1;
      });
    };
    rowList().forEach((row) => { row.querySelector("[data-row-controls]").hidden = false; });
    updateMoves();
    search.addEventListener("input", () => {
      const query = search.value.trim().toLocaleLowerCase();
      for (const row of rowList()) row.hidden = !row.dataset.builderName.toLocaleLowerCase().includes(query);
      const count = rowList().filter((row) => !row.hidden).length;
      hint.textContent = count ? count + " matches." : "No items match.";
      updateMoves();
    });
    rows.addEventListener("click", (event) => {
      const move = event.target.closest("[data-move]");
      if (!move) return;
      const row = move.closest("[data-builder-row]");
      const visible = rowList().filter((entry) => !entry.hidden);
      const direction = Number(move.dataset.move);
      const other = visible[visible.indexOf(row) + direction];
      if (!other) return;
      other.insertAdjacentElement(direction < 0 ? "beforebegin" : "afterend", row);
      form.elements[list.dataset.builderList + "_order"].value = rowList().map((entry) => entry.dataset.builderRow).join(",");
      updateMoves();
      const focus = move.disabled ? row.querySelector('[data-move="' + -direction + '"]') : move;
      focus.focus();
      hint.textContent = row.dataset.builderName + " moved to position " + (rowList().indexOf(row) + 1) + ".";
      change();
    });
  }

  for (const panel of editor.querySelectorAll("[data-confirm-panel]")) {
    if (typeof HTMLDialogElement === "undefined") continue;
    const trigger = editor.querySelector('[data-builder-confirm="' + panel.id + '"]');
    const dialog = document.createElement("dialog");
    dialog.className = panel.className;
    dialog.setAttribute("aria-labelledby", panel.getAttribute("aria-labelledby"));
    dialog.append(...panel.childNodes);
    panel.replaceWith(dialog);
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      if (!dirty.hidden) {
        dirty.textContent = "Unsaved changes. Save the draft before publishing.";
        form.querySelector('input:not([type=hidden]):not([type=file])')?.focus();
        return;
      }
      dialog.showModal();
      dialog.querySelector("[data-confirm-cancel]").focus();
    });
    dialog.querySelector("[data-confirm-cancel]").addEventListener("click", (event) => {
      event.preventDefault();
      dialog.close();
    });
    dialog.addEventListener("close", () => trigger.focus());
  }
});
