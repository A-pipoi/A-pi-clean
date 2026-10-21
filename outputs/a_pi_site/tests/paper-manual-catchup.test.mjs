import assert from "node:assert/strict";
import test from "node:test";
import { JSDOM } from "jsdom";
import { configureApiClient } from "../dist/api-client.mjs";
import {
  beijingDay,
  manualCatchupEligibility,
  manualCatchupMessage,
  createManualPaperCatchup,
} from "../dist/paper-manual-catchup.mjs";

const now = new Date("2026-10-14T13:00:00Z");
const scheduler = {
  enabled: true,
  maintenance: {
    latest: { run_date: "2026-10-14", status: "missed_window", attempts: 0 },
  },
};

test("eligibility uses Beijing calendar date and never infers exchange open", () => {
  assert.equal(beijingDay(now), "2026-10-14");
  assert.equal(manualCatchupEligibility(scheduler, now).allowed, true);
  for (const state of [
    null,
    { ...scheduler, execution_in_progress: true },
    { ...scheduler, enabled: false },
    {
      maintenance: { latest: { ...scheduler.maintenance.latest, attempts: 1 } },
    },
    {
      maintenance: {
        latest: { ...scheduler.maintenance.latest, status: "completed" },
      },
    },
    {
      ...scheduler,
      maintenance: {
        ...scheduler.maintenance,
        manual_catchups: [{ run_date: "2026-10-14", status: "failed" }],
      },
    },
  ])
    assert.equal(manualCatchupEligibility(state, now).allowed, false);
  assert.equal(
    manualCatchupEligibility(scheduler, new Date("2026-10-14T08:10:59Z"))
      .allowed,
    false,
  );
  assert.equal(
    manualCatchupEligibility(scheduler, new Date("2026-10-14T16:00:00Z"))
      .allowed,
    false,
  );
  assert.equal(
    manualCatchupEligibility(
      {
        maintenance: {
          latest: {
            run_date: "2026-10-17",
            status: "missed_window",
            attempts: 0,
          },
        },
      },
      new Date("2026-10-17T13:00:00Z"),
    ).allowed,
    false,
  );
});

test("blocked, failed and incomplete responses do not claim completion", () => {
  assert.match(
    manualCatchupMessage({ outcome: "blocked", skip_reason: "密封未通过" }),
    /被阻止.*密封未通过/,
  );
  assert.match(manualCatchupMessage({ outcome: "failed" }), /失败/);
  assert.match(manualCatchupMessage({ outcome: "already_running" }), /未启动/);
  assert.match(manualCatchupMessage({}), /未确认/);
  assert.match(
    manualCatchupMessage({ skipped: false, generated_orders: 0 }),
    /已完成.*未补造成交/,
  );
});

test("confirmation is required, cancel is harmless, requests are authenticated and one shot", async () => {
  const dom = new JSDOM(
    '<button id="paperManualCatchupButton">人工补跑</button><p id="paperManualCatchupHint"></p><p id="paperManualCatchupResult"></p>',
    { url: "https://127.0.0.1:8000" },
  );
  const previous = {
    document: globalThis.document,
    HTMLElement: globalThis.HTMLElement,
  };
  globalThis.document = dom.window.document;
  globalThis.HTMLElement = dom.window.HTMLElement;
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  dom.window.HTMLDialogElement.prototype.close = function () {
    this.open = false;
    this.dispatchEvent(new dom.window.Event("close"));
  };
  const requests = [];
  let release;
  const waiting = new Promise((resolve) => {
    release = resolve;
  });
  const client = configureApiClient({
    origin: dom.window.location.href,
    onExpired() {},
    fetch: async (url, options) => {
      if (url === "/api/auth/session")
        return Response.json({
          authenticated: true,
          username: "fixture",
          role: "user",
          expires_at: "2030-01-01",
        });
      requests.push({ url, options });
      await waiting;
      return Response.json({
        ok: true,
        skipped: true,
        outcome: "blocked",
        skip_reason: "<img src=x onerror=alert(1)>",
      });
    },
  });
  try {
    await client.boot();
    let applied = 0,
      refreshed = 0;
    const busy = [];
    const ui = createManualPaperCatchup({
      translate: (s) => s,
      clock: () => now,
      apply: () => {
        applied++;
      },
      refresh: async () => {
        refreshed++;
      },
      onBusy: (value) => busy.push(value),
    });
    ui.update(scheduler);
    const button = document.getElementById("paperManualCatchupButton");
    button.focus();
    button.click();
    assert.equal(requests.length, 0);
    let dialog = document.querySelector("dialog");
    assert.match(dialog.textContent, /正式纸面账户.*不撮合旧委托/);
    assert.equal(document.activeElement.textContent, "取消");
    dialog.querySelector(".secondary-button").click();
    assert.equal(document.querySelector("dialog"), null);
    assert.equal(document.activeElement, button);
    button.click();
    dialog = document.querySelector("dialog");
    dialog.querySelector(".primary-button").click();
    dialog.querySelector(".primary-button").click();
    assert.equal(requests.length, 1);
    assert.equal(button.disabled, true);
    assert.equal(requests[0].url, "/api/paper/cycle");
    assert.equal(requests[0].options.credentials, "same-origin");
    assert.equal(requests[0].options.headers.get("X-A-Pi-Request"), "1");
    assert.deepEqual(JSON.parse(requests[0].options.body), {
      manual_catchup: true,
      expected_day: "2026-10-14",
      reason: "用户在已登录工作台确认当日人工补跑",
    });
    const cancel = new dom.window.Event("cancel", { cancelable: true });
    dialog.dispatchEvent(cancel);
    assert.equal(cancel.defaultPrevented, true);
    release();
    for (let i = 0; i < 20 && refreshed === 0; i++)
      await new Promise((resolve) => setImmediate(resolve));
    assert.equal(applied, 1);
    assert.equal(refreshed, 1);
    assert.deepEqual(busy, [true, false]);
    assert.match(
      document.getElementById("paperManualCatchupResult").textContent,
      /被阻止.*<img/,
    );
    assert.equal(document.querySelector("img"), null);
    dialog.querySelector(".secondary-button").click();
    button.click();
    assert.equal(requests.length, 1);
    assert.equal(document.querySelector("dialog"), null);
  } finally {
    globalThis.document = previous.document;
    globalThis.HTMLElement = previous.HTMLElement;
    dom.window.close();
  }
});

test("cross-day confirmation sends nothing; lost response stays unknown without duplicate retry", async () => {
  const dom = new JSDOM(
    '<button id="paperManualCatchupButton">人工补跑</button><p id="paperManualCatchupHint"></p><p id="paperManualCatchupResult"></p>',
    { url: "https://127.0.0.1:8000" },
  );
  const previous = {
    document: globalThis.document,
    HTMLElement: globalThis.HTMLElement,
  };
  globalThis.document = dom.window.document;
  globalThis.HTMLElement = dom.window.HTMLElement;
  dom.window.HTMLDialogElement.prototype.showModal = function () {
    this.open = true;
  };
  dom.window.HTMLDialogElement.prototype.close = function () {
    this.dispatchEvent(new dom.window.Event("close"));
  };
  let current = now,
    requests = 0,
    refreshed = 0;
  const client = configureApiClient({
    origin: dom.window.location.href,
    onExpired() {},
    fetch: async (url) => {
      if (url === "/api/auth/session")
        return Response.json({
          authenticated: true,
          username: "fixture",
          role: "user",
          expires_at: "2030-01-01",
        });
      requests++;
      throw new Error("connection_lost");
    },
  });
  try {
    await client.boot();
    const ui = createManualPaperCatchup({
      translate: (s) => s,
      clock: () => current,
      apply: () => {
        throw new Error("must not apply unknown result");
      },
      refresh: async () => {
        refreshed++;
      },
    });
    ui.update(scheduler);
    const button = document.getElementById("paperManualCatchupButton");
    button.click();
    current = new Date("2026-10-14T16:00:00Z");
    document.querySelector("dialog .primary-button").click();
    assert.equal(requests, 0);
    assert.match(
      document.querySelector("dialog").textContent,
      /日期或维护状态已变化/,
    );
    document.querySelector("dialog .secondary-button").click();
    current = now;
    ui.update(scheduler);
    button.click();
    document.querySelector("dialog .primary-button").click();
    for (let i = 0; i < 20 && refreshed === 0; i++)
      await new Promise((resolve) => setImmediate(resolve));
    assert.equal(requests, 1);
    assert.equal(refreshed, 1);
    assert.match(
      document.getElementById("paperManualCatchupResult").textContent,
      /结果未确认.*不要重复提交/,
    );
    assert.equal(button.disabled, true);
    assert.equal(
      document.querySelector("dialog .secondary-button").disabled,
      false,
    );
  } finally {
    globalThis.document = previous.document;
    globalThis.HTMLElement = previous.HTMLElement;
    dom.window.close();
  }
});
