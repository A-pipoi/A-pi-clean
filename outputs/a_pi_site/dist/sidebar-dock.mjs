/** Enhance the existing navigation, not a modal: no focus trap or page reflow.
 * @param {Document} [root] */
export function installSidebarDock(root = document) {
  const sidebar = root.querySelector(".app-shell > .sidebar");
  if (!(sidebar instanceof HTMLElement)) return null;
  const view = root.defaultView;
  if (!view) return null;
  const desktop = view.matchMedia("(min-width: 721px)");
  const hover = view.matchMedia("(hover: hover) and (pointer: fine)");
  const dock = root.createElement("div");
  dock.className = "sidebar-dock";
  dock.dataset.open = "false";
  sidebar.id ||= "sidebarNavigation";
  sidebar.setAttribute("aria-label", "主导航");
  const handle = root.createElement("button");
  handle.type = "button";
  handle.className = "sidebar-handle";
  handle.setAttribute("aria-controls", sidebar.id);
  handle.setAttribute("aria-label", "展开并固定导航栏");
  handle.title = "悬停展开，点击固定导航栏";
  const svg = root.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = root.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("d", "m9 6 6 6-6 6");
  svg.append(path);
  handle.append(svg);
  sidebar.before(dock);
  dock.append(sidebar, handle);
  let open = false;
  let pinned = false;
  let pointerInside = false;
  let closeTimer = 0;
  const cancelClose = () => view.clearTimeout(closeTimer);
  /** @param {boolean} next @param {boolean} [instant] */
  const setOpen = (next, instant = false) => {
    cancelClose();
    if (!desktop.matches) next = false;
    if (!next && sidebar.contains(root.activeElement)) handle.focus();
    open = next;
    dock.dataset.instant = String(instant);
    dock.dataset.open = String(open);
    sidebar.inert = desktop.matches && !open;
    handle.setAttribute("aria-expanded", String(open));
    handle.setAttribute("aria-pressed", String(pinned));
    handle.setAttribute(
      "aria-label",
      pinned ? "收起导航栏" : "展开并固定导航栏",
    );
    handle.title = pinned ? "点击取消固定并收起" : "悬停展开，点击固定导航栏";
  };
  const scheduleClose = () => {
    cancelClose();
    closeTimer = view.setTimeout(() => {
      if (
        !pinned &&
        !(
          dock.contains(root.activeElement) &&
          root.activeElement?.matches(":focus-visible")
        ) &&
        !pointerInside
      )
        setOpen(false);
    }, 220);
  };
  dock.addEventListener("pointerenter", (event) => {
    pointerInside = event.pointerType !== "touch";
    if (desktop.matches && hover.matches && event.pointerType !== "touch")
      setOpen(true);
  });
  dock.addEventListener("pointerleave", () => {
    pointerInside = false;
    scheduleClose();
  });
  dock.addEventListener("focusin", () => {
    // A mouse click can focus a control too; only keyboard focus skips movement.
    if (desktop.matches)
      setOpen(
        true,
        handle.matches(":focus-visible") ||
          Boolean(root.activeElement?.matches(":focus-visible")),
      );
  });
  dock.addEventListener("focusout", scheduleClose);
  handle.addEventListener("click", (event) => {
    pinned = !pinned;
    setOpen(pinned, event.detail === 0);
  });
  dock.addEventListener("keydown", (event) => {
    if (event.key !== "Escape" || !desktop.matches || !open) return;
    event.preventDefault();
    pinned = false;
    handle.focus();
    setOpen(false, true);
  });
  /** @param {PointerEvent} event */
  const outside = (event) => {
    if (
      !desktop.matches ||
      !open ||
      (event.target instanceof Node && dock.contains(event.target))
    )
      return;
    pinned = false;
    setOpen(false);
  };
  root.addEventListener("pointerdown", outside);
  const reset = () => {
    pointerInside = false;
    pinned = false;
    setOpen(false, true);
  };
  desktop.addEventListener("change", reset);
  reset();
  return {
    dock,
    handle,
    sidebar,
    get open() {
      return open;
    },
    get pinned() {
      return pinned;
    },
  };
}
