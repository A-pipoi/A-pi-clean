import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { installSidebarDock } from "../dist/sidebar-dock.mjs";

function setup() {
  const dom = new JSDOM('<div class="app-shell"><aside class="sidebar"><button data-section="paper">纸面交易</button></aside><main class="main-content"><button id="outside">内容</button></main></div>', { pretendToBeVisual: true });
  for (const key of ["document", "HTMLElement", "Node"]) globalThis[key] = dom.window[key];
  const media = new Map();
  dom.window.matchMedia = (query) => {
    if (!media.has(query)) media.set(query, { matches: true, listeners: [], addEventListener(_event, callback) { this.listeners.push(callback); } });
    return media.get(query);
  };
  const navigation = document.querySelector("[data-section]");
  let navigated = 0;
  navigation.addEventListener("click", () => navigated++);
  const controller = installSidebarDock();
  const pointer = (target, type, pointerType = "mouse") => {
    const event = new dom.window.Event(type, { bubbles: true });
    Object.defineProperty(event, "pointerType", { value: pointerType });
    target.dispatchEvent(event);
  };
  return { dom, controller, media, navigation, pointer, get navigated() { return navigated; } };
}

test("sidebar hides without cloning navigation; hover opens and delayed leave is interruptible", async () => {
  const fixture = setup(), { controller, pointer } = fixture;
  assert.equal(controller.open, false);
  assert.equal(controller.sidebar.inert, true);
  assert.equal(controller.handle.getAttribute("aria-expanded"), "false");
  assert.equal(controller.sidebar.querySelector("button"), fixture.navigation);
  pointer(controller.dock, "pointerenter");
  assert.equal(controller.open, true);
  assert.equal(controller.sidebar.inert, false);
  fixture.navigation.click();
  assert.equal(fixture.navigated, 1);
  pointer(controller.dock, "pointerleave");
  assert.equal(controller.open, true);
  pointer(controller.dock, "pointerenter");
  await new Promise((resolve) => setTimeout(resolve, 240));
  assert.equal(controller.open, true);
  pointer(controller.dock, "pointerleave");
  await new Promise((resolve) => setTimeout(resolve, 240));
  assert.equal(controller.open, false);
  fixture.dom.window.close();
});

test("click pins; keyboard opens instantly and Escape returns focus to the handle", async () => {
  const fixture = setup(), { controller, pointer, dom } = fixture;
  controller.handle.click();
  assert.equal(controller.pinned, true);
  assert.equal(controller.open, true);
  pointer(controller.dock, "pointerleave");
  await new Promise((resolve) => setTimeout(resolve, 240));
  assert.equal(controller.open, true);
  fixture.navigation.focus();
  fixture.navigation.dispatchEvent(new dom.window.KeyboardEvent("keydown", { key: "Escape", bubbles: true }));
  assert.equal(controller.open, false);
  assert.equal(document.activeElement, controller.handle);
  assert.equal(controller.dock.dataset.instant, "true");
  controller.handle.blur();
  controller.handle.focus();
  assert.equal(controller.open, true);
  pointer(document.getElementById("outside"), "pointerdown");
  assert.equal(controller.open, false);
  dom.window.close();
});

test("touch doesn't activate hover and mobile restores visible non-inert navigation", () => {
  const fixture = setup(), { controller, pointer, media } = fixture;
  pointer(controller.dock, "pointerenter", "touch");
  assert.equal(controller.open, false);
  const desktop = media.get("(min-width: 721px)");
  desktop.matches = false;
  desktop.listeners.forEach((callback) => callback());
  assert.equal(controller.sidebar.inert, false);
  pointer(controller.dock, "pointerenter");
  assert.equal(controller.open, false);
  assert.equal(installSidebarDock(), null);
  fixture.dom.window.close();
});
