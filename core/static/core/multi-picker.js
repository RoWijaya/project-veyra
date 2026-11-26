
document.querySelectorAll("[data-multi-picker]").forEach((field) => {
  const select = field.querySelector("select");
  const picker = field.querySelector("[data-picker]");
  const label = field.querySelector("label");
  const toggle = picker.querySelector("[data-toggle]");
  const summary = picker.querySelector("[data-summary]");
  const panel = picker.querySelector("[data-panel]");
  const search = picker.querySelector("[data-search]");
  const list = picker.querySelector("[data-options]");
  const empty = picker.querySelector("[data-no-matches]");
  const selection = picker.querySelector("[data-selection]");
  const chips = picker.querySelector("[data-chips]");
  const clear = picker.querySelector("[data-clear]");
  const status = picker.querySelector("[data-picker-status]");
  const choiceTemplate = picker.querySelector("[data-choice]");
  const chipTemplate = picker.querySelector("[data-chip]");
  const { singular, plural } = field.dataset;
  const normalize = (value) => value.normalize("NFD").replace(/\p{M}/gu, "").toLocaleLowerCase().trim();
  let matches = [];
  let activeChoice;

  const choices = Array.from(select.options).filter((option) => select.multiple || option.value).map((option) => {
    const button = choiceTemplate.content.firstElementChild.cloneNode(true);
    const name = option.label;
    button.firstElementChild.textContent = name;
    button.disabled = select.disabled || option.disabled;
    list.append(button);
    return { option, button, name, searchText: normalize(name) };
  });

  function announce(message) {
    status.textContent = message;
  }

  function renderSelection() {
    const selected = choices.filter(({ option }) => option.selected);
    const fragment = document.createDocumentFragment();
    for (const choice of choices) {
      choice.button.setAttribute("aria-selected", String(choice.option.selected));
    }
    if (!select.multiple) {
      selection.hidden = !selected.length;
      clear.hidden = selected.every(({ button }) => button.disabled);
      summary.textContent = selected[0]?.name || (choices.length ? `Choose a ${singular}` : `No ${plural} available`);
      return;
    }
    for (const [index, choice] of selected.entries()) {
      const chip = chipTemplate.content.firstElementChild.cloneNode(true);
      chip.firstElementChild.textContent = choice.name;
      chip.setAttribute("aria-label", `Remove ${choice.name}`);
      chip.disabled = choice.button.disabled;
      chip.addEventListener("click", () => {
        changeChoice(choice);
        (chips.children[index] || chips.lastElementChild || toggle).focus();
      });
      fragment.append(chip);
    }
    chips.replaceChildren(fragment);
    selection.hidden = selected.length === 0;
    clear.hidden = selected.filter(({ button }) => !button.disabled).length < 2;
    summary.textContent = selected.length
      ? `${selected.length} ${selected.length === 1 ? singular : plural} selected`
      : choices.length ? `Choose ${plural}` : `No ${plural} available`;
  }

  function changeChoice({ option }) {
    if (select.multiple) option.selected = !option.selected;
    else select.value = option.value;
    select.dispatchEvent(new Event("change", { bubbles: true }));
    announce(summary.textContent);
    if (!select.multiple) {
      setOpen(false);
      toggle.focus();
    }
  }

  function setActiveChoice(choice) {
    if (activeChoice) activeChoice.button.tabIndex = -1;
    activeChoice = choice;
    if (choice) choice.button.tabIndex = 0;
  }

  function filterChoices() {
    const query = normalize(search.value);
    matches = choices.filter(({ button, searchText }) => {
      button.hidden = !searchText.includes(query);
      return !button.hidden && !button.disabled;
    });
    empty.hidden = choices.some(({ button }) => !button.hidden);
    setActiveChoice(matches[0]);
  }

  function setOpen(open) {
    if (panel.hidden === !open) return;
    panel.hidden = !open;
    toggle.setAttribute("aria-expanded", String(open));
    if (open) {
      search.value = "";
      filterChoices();
      search.focus();
    }
  }

  toggle.disabled = select.disabled || choices.every(({ button }) => button.disabled);
  toggle.addEventListener("click", () => setOpen(panel.hidden));
  search.addEventListener("input", () => {
    filterChoices();
    announce(empty.hidden ? `${matches.length} matching ${matches.length === 1 ? singular : plural}.` : empty.textContent);
  });
  search.addEventListener("keydown", (event) => {
    if (event.isComposing || !["ArrowDown", "ArrowUp", "Enter"].includes(event.key)) return;
    event.preventDefault();
    const choice = event.key === "ArrowUp" ? matches.at(-1) : matches[0];
    if (!choice) return;
    if (event.key === "Enter") changeChoice(choice);
    else choice.button.focus();
  });

  for (const choice of choices) {
    choice.button.addEventListener("focus", () => setActiveChoice(choice));
    choice.button.addEventListener("click", () => changeChoice(choice));
  }
  list.addEventListener("keydown", (event) => {
    const index = matches.indexOf(activeChoice);
    const next = {
      ArrowUp: index - 1, ArrowDown: index + 1, Home: 0, End: matches.length - 1,
    }[event.key];
    if (next === undefined || index < 0) return;
    event.preventDefault();
    matches[Math.max(0, Math.min(next, matches.length - 1))].button.focus();
  });

  clear.addEventListener("click", () => {
    for (const { option, button } of choices) {
      if (!button.disabled) option.selected = false;
    }
    if (!select.multiple) select.value = "";
    select.dispatchEvent(new Event("change", { bubbles: true }));
    announce(`No ${plural} selected.`);
    setOpen(false);
    toggle.focus();
  });
  field.addEventListener("keydown", (event) => {
    if (panel.hidden || event.isComposing) return;
    if (event.key === "Tab") {
      // Let focus move before collapsing the in-flow panel.
      requestAnimationFrame(() => {
        if (!field.contains(document.activeElement)) setOpen(false);
      });
    } else if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      toggle.focus();
    }
  });
  document.addEventListener("click", (event) => {
    // A removed chip still belongs to this click's original event path.
    if (!event.composedPath().includes(field)) setOpen(false);
  });
  select.addEventListener("change", renderSelection);
  window.addEventListener("pageshow", renderSelection);
  renderSelection();
  picker.hidden = false;
  label.htmlFor = toggle.id;
  select.hidden = true;
});
