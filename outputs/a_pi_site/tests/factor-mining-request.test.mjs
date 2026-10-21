import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { configureApiClient } from "../dist/api-client.mjs";

test("network ambiguity, 429 and background responses never claim failed or completed research", async () => {
  for (const scenario of ["network", "busy", "background", "accepted"]) {
    const dom = new JSDOM(
      readFileSync(new URL("../dist/index.html", import.meta.url), "utf8"),
      { url: "http://localhost:8000" },
    );
    const saved = new Map();
    for (const key of [
      "window",
      "document",
      "HTMLElement",
      "HTMLInputElement",
      "HTMLSelectElement",
      "HTMLTextAreaElement",
      "Element",
      "Node",
    ]) {
      saved.set(key, globalThis[key]);
      globalThis[key] = dom.window[key];
    }
    const timers = [];
    dom.window.setTimeout = (fn) => {
      timers.push(fn);
      return timers.length;
    };
    dom.window.clearTimeout = () => {};
    let posts = 0;
    const snapshot = {
      ok: true,
      runs: [],
      factor_library: [],
      expression_templates: [],
    };
    const job = {
      job_id: "j1",
      status: "running",
      stage: "daily_bars",
      progress_current: 1,
      progress_total: 4,
    };
    const client = configureApiClient({
      origin: "http://localhost:8000",
      fetch: async (path, options) => {
        if (path === "/api/auth/session")
          return new Response(
            JSON.stringify({
              authenticated: true,
              username: "alice",
              role: "user",
              expires_at: "2030-01-01",
            }),
          );
        if (options?.method === "POST") {
          posts++;
          if (scenario === "network") throw new TypeError("Failed to fetch");
          if (scenario === "busy")
            return new Response(
              JSON.stringify({ ok: false, error: "operation_already_active" }),
              { status: 429, headers: { "Retry-After": "180" } },
            );
          return new Response(
            JSON.stringify({
              ok: true,
              job_id: "j1",
              stage: "dataset_sealing",
            }),
            { status: 202 },
          );
        }
        return new Response(
          JSON.stringify(
            scenario === "background" || (scenario === "accepted" && posts > 0)
              ? { ...snapshot, active_job: job, latest_job: job }
              : snapshot,
          ),
        );
      },
    });
    try {
      await client.boot();
      const panel = await import(
        `../dist/factor-mining-panel.mjs?scenario=${scenario}`
      );
      const toasts = [];
      panel.initFactorMiningPanel({
        escapeHtml: String,
        localeText: String,
        setText: (id, value) => {
          dom.window.document.getElementById(id).textContent = value;
        },
        showToast: (value) => toasts.push(value),
        closestHTMLElement: (target) => target,
        loadFactorDefinitions: async () => {},
      });
      await panel.loadFactorMining();
      dom.window.document.getElementById("factorMineButton").click();
      // Initial status deliberately has no job; the POST starts it.
      if (scenario === "background" && posts === 0) {
        // Simulate a reload while a job is active: controls must stay disabled.
        assert.equal(
          dom.window.document.getElementById("factorMineButton").disabled,
          true,
        );
        assert.equal(
          dom.window.document.getElementById("factorMiningCancelButton").hidden,
          false,
        );
      } else {
        for (let index = 0; index < 20 && !timers.length; index++)
          await new Promise((resolve) => setImmediate(resolve));
        assert.equal(posts, 1);
        assert.ok(timers.length);
        assert.doesNotMatch(toasts.join(" "), /因子研究失败|已完成/);
        assert.match(
          dom.window.document.getElementById("factorMiningFeedback")
            .textContent,
          scenario === "network"
            ? /尚未确认/
            : scenario === "accepted"
              ? /后台|数据|行情/
              : /正在执行/,
        );
      }
    } finally {
      dom.window.close();
      for (const [key, value] of saved) {
        if (value === undefined) delete globalThis[key];
        else globalThis[key] = value;
      }
    }
  }
});
