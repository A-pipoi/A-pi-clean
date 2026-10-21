import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { createDefaultBacktestDisplay } from "../dist/backtest-default-display.mjs";
import { configureApiClient } from "../dist/api-client.mjs";

test("default display requires explicit success selection; failure keeps previous result", async () => {
  const dom = new JSDOM(
    '<div class="page-heading"><span id="backtestStatus"></span></div><div class="metric-grid"><article class="metric-card"><span id="portfolioEquity"></span></article></div>',
  );
  const oldDocument = globalThis.document,
    oldFetch = globalThis.fetch;
  globalThis.document = dom.window.document;
  const applied = [],
    requests = [];
  const result = {
    job_id: "BT-one",
    strategy: "baseline",
    strategy_label: "基准",
    strategy_version: "baseline-v2",
    completed_at: "2026-10-13T08:00:00Z",
    metrics: {},
    equity_curve: [],
  };
  let fail = false;
  globalThis.fetch = async (url, options) => {
    if (url === "/api/auth/session")
      return new Response(
        JSON.stringify({
          authenticated: true,
          username: "qa",
          role: "researcher",
          expires_at: "2030-01-01",
        }),
      );
    requests.push([url, options]);
    if (options.method === "POST" && fail)
      return {
        ok: false,
        status: 400,
        json: async () => ({ ok: false, error: "任务未成功" }),
      };
    return {
      ok: true,
      json: async () => ({
        ok: true,
        display:
          options.method === "POST"
            ? {
                job_id: "BT-one",
                strategy_name: "基准",
                strategy_version: "baseline-v2",
                backtest_date: result.completed_at,
                result,
              }
            : null,
      }),
    };
  };
  try {
    const client = configureApiClient({
      fetch: globalThis.fetch,
      origin: "https://127.0.0.1:18443",
      onExpired: () => {},
    });
    await client.boot();
    const ui = createDefaultBacktestDisplay({
      localeText: (x) => x,
      applyPortfolioSnapshot: (x) => applied.push(x),
    });
    const button = document.querySelector("button");
    assert.equal(button.disabled, true);
    ui.showCandidate(result);
    assert.equal(applied.length, 0);
    ui.applyWorkspace();
    await new Promise((resolve) => setTimeout(resolve, 0));
    button.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(applied.length, 1);
    assert.match(
      document.getElementById("portfolioStrategyIdentity").textContent,
      /baseline-v2/,
    );
    assert.match(
      document.getElementById("portfolioStrategyIdentity").textContent,
      /16:00/,
    );
    ui.showCandidate({
      ...result,
      job_id: "BT-failed",
      strategy_version: "different",
    });
    fail = true;
    button.click();
    button.click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(requests.filter(([, o]) => o.method === "POST").length, 2);
    assert.equal(applied.length, 1);
    assert.match(
      document.getElementById("defaultBacktestNotice").textContent,
      /原默认展示未改变/,
    );
    assert.match(
      document.getElementById("portfolioStrategyIdentity").textContent,
      /baseline-v2/,
    );
  } finally {
    globalThis.document = oldDocument;
    globalThis.fetch = oldFetch;
    dom.window.close();
  }
});
