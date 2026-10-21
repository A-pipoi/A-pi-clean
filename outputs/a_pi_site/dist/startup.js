// Runs before first paint and is compatible with script-src 'self'.
try {
  const choice = localStorage.getItem("a_pi_theme");
  document.documentElement.dataset.theme =
    choice === "dark" || choice === "light"
      ? choice
      : matchMedia("(prefers-color-scheme: dark)").matches
        ? "dark"
        : "light";
} catch {
  document.documentElement.dataset.theme = "light";
}

// Independent of app boot, authentication and market requests. A broken module
// must never leave an unskippable overlay in front of the sign-in screen.
(() => {
  const root = document.documentElement;
  if (!root.hasAttribute("data-splash-enabled")) return;
  const motion = matchMedia("(prefers-reduced-motion: reduce)");
  const navigation = performance.getEntriesByType("navigation")[0];
  let seen = false;
  try {
    seen = sessionStorage.getItem("a_pi_opening_seen") === "1";
    sessionStorage.setItem("a_pi_opening_seen", "1");
  } catch {
    // Navigation type still prevents replays on reload when storage is blocked.
  }
  if (
    seen ||
    motion.matches ||
    (navigation instanceof PerformanceNavigationTiming &&
      navigation.type !== "navigate")
  )
    return;

  root.classList.add("desktop-startup");
  let finished = false;
  let leaving = false;
  let timer = 0;
  let deadline = 0;
  /** @type {MutationObserver | undefined} */
  let observer;
  /** @type {{element: HTMLElement, inert: boolean}[]} */
  const covered = [];

  function finish() {
    if (finished) return;
    finished = true;
    window.clearTimeout(timer);
    window.clearTimeout(deadline);
    observer?.disconnect();
    const moveFocus = document.activeElement?.id === "desktopSplashSkip";
    document.getElementById("desktopSplash")?.remove();
    root.classList.remove("desktop-startup");
    for (const { element, inert } of covered) element.inert = inert;
    document.removeEventListener("keydown", onKey);
    window.removeEventListener("error", onFailure, true);
    window.removeEventListener("unhandledrejection", onFailure);
    motion.removeEventListener("change", onMotion);
    if (moveFocus)
      document.getElementById("authUsername")?.focus({ preventScroll: true });
  }
  function leave() {
    if (finished || leaving) return;
    leaving = true;
    document.getElementById("desktopSplash")?.classList.add("is-leaving");
    timer = window.setTimeout(finish, 500);
  }
  /** @param {KeyboardEvent} event */
  function onKey(event) {
    if (event.key === "Escape") {
      event.preventDefault();
      finish();
    }
  }
  function onFailure() {
    finish();
  }
  function onMotion() {
    if (motion.matches) finish();
  }
  window.addEventListener("error", onFailure, true);
  window.addEventListener("unhandledrejection", onFailure);
  motion.addEventListener("change", onMotion);

  function mount() {
    if (finished) return;
    const splash = document.getElementById("desktopSplash");
    const skip = document.getElementById("desktopSplashSkip");
    if (!splash || !skip) {
      finish();
      return;
    }
    for (const id of ["authView", "appShell"]) {
      const element = document.getElementById(id);
      if (element) {
        covered.push({ element, inert: element.inert });
        element.inert = true;
      }
    }
    skip.addEventListener("click", finish, { once: true });
    document.addEventListener("keydown", onKey);
    const authError = document.getElementById("authError");
    if (authError) {
      observer = new MutationObserver(() => {
        if (!authError.hidden && authError.textContent?.trim()) finish();
      });
      observer.observe(authError, {
        attributes: true,
        childList: true,
        subtree: true,
        characterData: true,
      });
      if (!authError.hidden && authError.textContent?.trim()) {
        finish();
        return;
      }
    }
    timer = window.setTimeout(leave, 1900);
    deadline = window.setTimeout(finish, 2500);
  }
  document.addEventListener("DOMContentLoaded", mount, { once: true });
})();
