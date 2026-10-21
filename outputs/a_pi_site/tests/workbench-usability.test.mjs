import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { installWorkbenchDisclosure, displayedStatusDetails } from "../dist/workbench-disclosure.mjs";
import { installWorkbenchIA } from "../dist/workbench-ia.mjs";
import { initNotificationCenter, renderNotifications, recordEvent } from "../dist/notification-center.mjs";

function environment(html) {
  const dom = new JSDOM(html, { url: "https://localhost", pretendToBeVisual: true });
  for (const key of ["window", "document", "Node", "Element", "HTMLElement", "MouseEvent", "MutationObserver", "sessionStorage", "localStorage", "requestAnimationFrame"])
    globalThis[key] = dom.window[key];
  return dom;
}

test("disclosure preserves inputs and handlers, keeps gates visible, and has a safe return path", () => {
  const dom = environment(readFileSync(new URL("../dist/index.html", import.meta.url), "utf8"));
  const input = document.getElementById("factorMiningSeed");
  input.value = "42";
  let edits = 0;
  input.addEventListener("change", () => edits++);
  installWorkbenchDisclosure();
  assert.equal(document.getElementById("factorMiningSeed"), input);
  assert.equal(input.value, "42");
  input.dispatchEvent(new window.Event("change"));
  assert.equal(edits, 1);
  assert.equal(input.closest("details").open, false);
  assert.equal(document.getElementById("factorMiningAcceptanceGate").closest(".workbench-disclosure"), null);
  assert.equal(document.getElementById("factorMineButton").closest(".workbench-disclosure"), null);
  installWorkbenchIA();
  const optimizerPanel = document.querySelector("#risk .optimizer-panel");
  assert.ok(optimizerPanel.closest('[role="tabpanel"]'));
  document.getElementById('tab-rk-opt').click();
  assert.equal(optimizerPanel.closest('[role="tabpanel"]').hidden, false);
  document.getElementById('tab-rk-limits').click();
  assert.equal(optimizerPanel.closest('[role="tabpanel"]').hidden, true);
  let navigations = 0;
  document.querySelector('[data-section="overview"]').addEventListener("click", () => navigations++);
  document.querySelector('.nav-item[data-section="overview"]').click();
  assert.equal(navigations, 1);
  const count = document.querySelectorAll(".workbench-disclosure").length;
  installWorkbenchDisclosure();
  assert.equal(document.querySelectorAll(".workbench-disclosure").length, count);
  dom.window.close();
});

test("notifications open full details, return and route; deletion persists per account and changed warnings resurface", () => {
  const dom = environment('<html data-username="qa-a"><div class="notification-wrap"><button id="notificationButton"></button><span id="notificationCount"></span><span id="notificationDot"></span><section id="notificationCenter" hidden><button id="markNotificationsRead"></button><button id="refreshNotifications"></button><div id="notificationList"></div></section></div></html>');
  const routes = [];
  const notificationState = { workspace: { warnings: ["完整警告：" + "很长的详细说明".repeat(30)] } };
  initNotificationCenter({ notificationState, escapeHtml: (value) => String(value).replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll('"', "&quot;"), getLocale: () => "zh-CN", applyLocale() {}, paperStatusLabel: (value) => value, localeText: (value) => value, showToast() {}, showSection: (value) => routes.push(value), checkServiceHealth: async () => true, loadWorkspace: async () => true, fetchPaperState: async () => true, closestHTMLElement: (event, selector) => event.target.closest(selector) });
  renderNotifications();
  document.getElementById("notificationButton").click();
  document.querySelector(".notification-arrow").click();
  assert.equal(document.getElementById("notificationCenter").hidden, false);
  assert.equal(document.querySelector(".notification-detail p").textContent, notificationState.workspace.warnings[0]);
  document.querySelector("[data-notification-back]").click();
  document.querySelector(".notification-item").click();
  document.querySelector("[data-notification-route]").click();
  assert.deepEqual(routes, ["overview"]);
  renderNotifications();
  document.querySelector("[data-notification-delete]").click();
  renderNotifications();
  assert.equal(document.querySelector(".notification-row"), null);
  document.documentElement.dataset.username = "qa-b";
  renderNotifications();
  assert.ok(document.querySelector(".notification-row"));
  document.documentElement.dataset.username = "qa-a";
  renderNotifications();
  assert.equal(document.querySelector(".notification-row"), null);
  notificationState.workspace.warnings[0] += " 新变化";
  renderNotifications();
  assert.ok(document.querySelector(".notification-row"));
  recordEvent('<img src=x onerror=alert(1)>', "paper", "safe-event");
  document.querySelector('[data-notification-id="event:safe-event"]').click();
  assert.equal(document.querySelector(".notification-detail img"), null);
  dom.window.close();
});

test("export reads displayed identity and unavailable states, never raw credentials", () => {
  const dom = environment('<div id="portfolioStrategyIdentity">策略 A · v1</div><div id="paperQuality">行情未知</div><input id="authPassword" value="SECRET"><textarea id="arenaCredentials">TOKEN</textarea>');
  const details = displayedStatusDetails();
  assert.equal(details.default_backtest, "策略 A · v1");
  assert.equal(details.paper.data_quality, "行情未知");
  assert.doesNotMatch(JSON.stringify(details), /SECRET|TOKEN/);
  dom.window.close();
});

test("hover styling does not alter the ring geometry, sidebar is fixed and scrolling is local", () => {
  const reference = readFileSync(new URL("../dist/reference-theme.css", import.meta.url), "utf8");
  const css = readFileSync(new URL("../dist/workbench-usability.css", import.meta.url), "utf8");
  assert.doesNotMatch(reference, /allocation-panel\[data-pie-active="true"\][^{]*allocation-donut/);
  assert.match(css, /position: fixed/);
  assert.match(css, /max-height: 360px/);
  assert.doesNotMatch(css, /animation:|marquee|scroll-behavior: smooth/);
});
