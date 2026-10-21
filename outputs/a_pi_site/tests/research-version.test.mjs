import test from "node:test";
import assert from "node:assert/strict";
import { JSDOM } from "jsdom";
import { configureApiClient } from "../dist/api-client.mjs";
import {
  initResearchPaper,
  refreshResearchPaper,
} from "../dist/research-paper-page.mjs";

test("new research version requires confirmation and the frozen hash; no automatic resume or cycle", async () => {
  const dom = new JSDOM(
    '<html lang="zh-CN"><section id="paper"><div class="paper-kpis"></div></section></html>',
    { url: "https://127.0.0.1:8000" },
  );
  const original = { document: globalThis.document, window: globalThis.window };
  globalThis.document = dom.window.document;
  globalThis.window = dom.window;
  const requests = [];
  let mismatch = true;
  let confirmation = false;
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
      if (url.includes("/orders"))
        return Response.json({ ok: true, orders: [], pages: 1, total: 0 });
      if (url.endsWith("/new-version")) mismatch = false;
      return Response.json({
        ok: true,
        enabled: false,
        version_mismatch: mismatch,
        configuration: {
          config_hash: mismatch ? "old-frozen-hash" : "new-frozen-hash",
          code_hash: mismatch ? "old-code" : "new-code",
          strategy: "baseline_cost_aware",
          rebalance: "weekly",
          enabled: false,
          ...(!mismatch
            ? { archive_id: "archive", version_started_at: "2026-10-14" }
            : {}),
        },
        paper: { equity: 1000000, cash: 1000000, positions: [] },
      });
    },
  });
  dom.window.confirm = (message) => {
    assert.match(message, /旧账户.*完整保留/);
    assert.match(message, /初始暂停/);
    return confirmation;
  };
  try {
    await client.boot();
    initResearchPaper();
    for (let i = 0; i < 5; i++)
      await new Promise((resolve) => setImmediate(resolve));
    const buttons = [...document.querySelectorAll("button")];
    const enable = buttons.find((b) => b.textContent === "启用研究账户");
    const create = document.querySelector('[data-slot="research-new-version"]');
    assert.equal(enable.disabled, true);
    assert.equal(create.disabled, false);
    await refreshResearchPaper("new-version");
    assert.equal(
      requests.filter((r) => r.options?.method === "POST").length,
      0,
    );
    confirmation = true;
    await refreshResearchPaper("new-version");
    const writes = requests.filter((r) => r.options?.method === "POST");
    assert.equal(writes.length, 1);
    assert.equal(writes[0].url, "/api/paper/research/new-version");
    assert.deepEqual(JSON.parse(writes[0].options.body), {
      expected_config_hash: "old-frozen-hash",
      confirm_new_version: true,
    });
    assert.equal(writes[0].options.credentials, "same-origin");
    assert.equal(
      new Headers(writes[0].options.headers).get("X-A-Pi-Request"),
      "1",
    );
    assert.equal(create.disabled, true);
    assert.equal(enable.disabled, false);
    assert.match(
      document.querySelector('[role="status"]').textContent,
      /新版本已建.*已暂停.*未验收/,
    );
    await refreshResearchPaper("new-version");
    assert.equal(
      requests.filter((r) => r.options?.method === "POST").length,
      1,
    );
    assert.equal(
      requests.some(
        (r) => r.url === "/api/paper/cycle" || r.url.endsWith("/enable"),
      ),
      false,
    );
  } finally {
    globalThis.document = original.document;
    globalThis.window = original.window;
    dom.window.close();
  }
});
