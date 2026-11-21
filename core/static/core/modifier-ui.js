const orderOptionControls = (() => {
  const currency = new Intl.NumberFormat("id-ID", { maximumFractionDigits: 2 });
  const inputs = "[data-modifier-option]";
  const money = (value) => "Rp " + currency.format(value);

  function evaluate(scope, location) {
    const groups = [...scope.querySelectorAll("[data-modifier-group]")];
    let surcharge = 0;
    let error = "";
    const names = [];
    groups.forEach((group) => {
      const locations = (group.dataset.modifierLocations || "").split(" ").filter(Boolean);
      if (location !== undefined && locations.length) {
        group.hidden = !locations.includes(location);
        group.disabled = group.hidden;
        if (!location) error = "Choose an order location to see available options.";
      }
      if (group.disabled) return;
      const selected = [...group.querySelectorAll(inputs + ":checked")].filter((input) => input.value);
      const min = Number(group.dataset.min);
      const max = Number(group.dataset.max);
      if (!error && (selected.length < min || selected.length > max)) {
        const name = group.querySelector("legend").textContent.trim();
        const limit = min === max ? String(min) : min + "–" + max;
        error = name + ": choose " + limit + " option(s).";
      }
      selected.forEach((input) => {
        surcharge += Number(input.dataset.surcharge);
        names.push(input.closest("label").querySelector("span").textContent.trim());
      });
    });
    return { surcharge, names, error };
  }

  function showError(scope, text) {
    const hint = scope.querySelector("[data-modifier-error]");
    if (!hint) return;
    if (hint.textContent !== text) hint.textContent = text;
    hint.hidden = !text;
  }

  function preview(form, validate = false) {
    const result = evaluate(form);
    const output = form.querySelector("[data-modifier-price]");
    if (output) output.textContent = money(Number(output.dataset.basePrice) + result.surcharge);
    if (validate) showError(form, result.error);
    return result;
  }

  const forms = "[data-configurable-item]";
  document.addEventListener("change", (event) => {
    if (event.target.matches(inputs) && event.target.closest(forms)) preview(event.target.closest(forms), true);
  });
  document.addEventListener("submit", (event) => {
    if (!event.target.matches(forms)) return;
    if (preview(event.target, true).error) event.preventDefault();
  }, true);
  document.addEventListener("DOMContentLoaded", () => document.querySelectorAll(forms).forEach((form) => preview(form)));
  document.addEventListener("modifiers:refresh", (event) => event.target.querySelectorAll(forms).forEach((form) => preview(form)));
  return { evaluate, inputs, money, showError };
})();
