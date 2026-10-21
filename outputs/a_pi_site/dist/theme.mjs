import { APiCharts } from "./charts.mjs";
/** @param {string|null} preference @param {boolean} systemDark */
export function resolveTheme(preference, systemDark) {
  return preference === "dark" || preference === "light"
    ? preference
    : systemDark
      ? "dark"
      : "light";
}
export function installTheme() {
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  /** @type {HTMLSelectElement[]} */
  const controls = [];
  let preference = "system";
  try {
    const saved = window.localStorage.getItem("a_pi_theme");
    if (saved === "light" || saved === "dark") preference = saved;
  } catch {}
  const apply = () => {
    document.documentElement.dataset.theme = resolveTheme(
      preference,
      media.matches,
    );
    for (const control of controls) control.value = preference;
    APiCharts.draw();
  };
  for (const host of document.querySelectorAll(".topbar,.auth-card-footer")) {
    const label = document.createElement("label");
    label.className = "theme-control";
    const caption = document.createElement("span");
    caption.textContent = "界面主题";
    const select = document.createElement("select");
    select.setAttribute("aria-label", "界面主题");
    for (const [value, text] of [
      ["system", "跟随系统"],
      ["light", "浅色"],
      ["dark", "暗色"],
    ]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = text;
      select.append(option);
    }
    controls.push(select);
    select.addEventListener("change", () => {
      preference = select.value;
      try {
        window.localStorage.setItem("a_pi_theme", preference);
      } catch {}
      apply();
    });
    label.append(caption, select);
    host.append(label);
  }
  window.addEventListener("storage", (event) => {
    if (event.key !== "a_pi_theme" && event.key !== null) return;
    preference =
      event.newValue === "light" || event.newValue === "dark"
        ? event.newValue
        : "system";
    apply();
  });
  media.addEventListener("change", apply);
  apply();
}
