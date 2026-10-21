import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { configureApiClient } from "../dist/api-client.mjs";
import {
  initResearchPaper,
  refreshResearchPaper,
} from "../dist/research-paper-page.mjs";

test("research initialization is read-only; enable and maintenance never address the formal account", async () => {
  const dom = new JSDOM(
    '<html lang="zh-CN"><body><section id="paper"><div class="paper-kpis"></div></section></body></html>',
  );
  const oldDocument = globalThis.document;
  globalThis.document = dom.window.document;
  const requests = [];
  let enabled = false;
  let pausedFinancial = false;
  let noOrders = false;
  const hostile = "<img src=x onerror=alert(1)>";
  const client = configureApiClient({
    origin: "http://localhost:8000",
    onExpired() {},
    fetch: async (url, options) => {
      if (url === "/api/auth/session")
        return Response.json({
          authenticated: true,
          username: "test",
          role: "user",
          expires_at: "2030-01-01",
        });
      requests.push({ url, options });
      if (url === "/api/paper/research/enable") enabled = true;
      if (url.startsWith("/api/paper/research/orders"))
        return Response.json({
          ok: true,
          orders: noOrders
            ? []
            : [
                {
                  order_id: "R1",
                  symbol: hostile,
                  side: "BUY",
                  quantity: 100,
                  filled_quantity: 0,
                  status: "pending",
                },
              ],
          pages: 1,
          total: noOrders ? 0 : 1,
        });
      return Response.json({
        ok: true,
        enabled,
        ...(pausedFinancial
          ? {
              configuration: {
                enabled: false,
                strategy: "baseline_cost_aware",
                rebalance: "weekly",
              },
              paper: { equity: 1000000, cash: 1000000, positions: [] },
            }
          : {}),
        ...(enabled
          ? {
              configuration: { strategy: "baseline", rebalance: "weekly" },
              paper: { equity: 1000000, cash: 1000000, positions: [] },
            }
          : {}),
        ...(url.endsWith("/cycle")
          ? { cycle: { skip_reason: "窗口外不补跑" } }
          : {}),
      });
    },
  });
  try {
    await client.boot();
    initResearchPaper();
    for (let i = 0; i < 4; i++)
      await new Promise((resolve) => setImmediate(resolve));
    assert.ok(
      requests.every(
        (request) =>
          request.url === "/api/paper/research" &&
          (request.options?.method || "GET") === "GET",
      ),
    );
    assert.match(document.querySelector("[role=status]").textContent, /未启用/);
    pausedFinancial = true;
    await refreshResearchPaper();
    const reviewButton = Array.from(document.querySelectorAll("button")).find(
      (button) => button.textContent.includes("升级财务低换手版本"),
    );
    assert.equal(reviewButton.disabled, false);
    pausedFinancial = false;
    await refreshResearchPaper("enable");
    assert.match(document.querySelector("[role=status]").textContent, /未验收/);
    assert.equal(document.querySelector("tbody img"), null);
    assert.ok(document.querySelector("tbody").textContent.includes(hostile));
    await refreshResearchPaper("cycle");
    const writes = requests.filter(
      (request) => request.options?.method === "POST",
    );
    assert.deepEqual(
      writes.map((request) => request.url),
      ["/api/paper/research/enable", "/api/paper/research/cycle"],
    );
    assert.ok(writes.every((request) => request.options.body === "{}"));
    assert.match(
      document.querySelector(".research-paper-panel").textContent,
      /窗口外不补跑/,
    );
    const upgrade = Array.from(document.querySelectorAll("button")).find(
      (button) => button.textContent.includes("升级财务低换手版本"),
    );
    assert.ok(upgrade);
    assert.match(
      document.querySelector(".research-paper-panel").textContent,
      /不回填历史，效果尚未验证/,
    );
    assert.equal(document.querySelectorAll(".research-balance-item").length, 4);
    assert.ok(
      document
        .querySelector(".research-execution-schedule")
        .textContent.includes("16:00–16:10"),
    );
    await refreshResearchPaper("upgrade-financial");
    assert.equal(
      requests.filter((request) => request.options?.method === "POST").at(-1)
        .url,
      "/api/paper/research/upgrade-financial",
    );
    noOrders = true;
    await refreshResearchPaper();
    const emptyRows = Array.from(
      document.querySelectorAll(".research-paper-panel tbody tr"),
    );
    assert.equal(emptyRows.length, 2);
    assert.match(emptyRows[0].textContent, /未验收研究纸面委托/);
    assert.match(emptyRows[1].textContent, /暂无研究纸面委托/);
    assert.ok(
      Array.from(document.querySelectorAll(".research-paper-panel th")).every(
        (cell) => cell.scope === "col",
      ),
    );
    assert.equal(document.querySelectorAll(".research-paper-panel").length, 1);
    initResearchPaper();
    assert.equal(document.querySelectorAll(".research-paper-panel").length, 1);
  } finally {
    globalThis.document = oldDocument;
    dom.window.close();
  }
});
