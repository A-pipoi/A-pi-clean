import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { configureApiClient } from "../dist/api-client.mjs";

test("saved training reports appear before acceptance and beside census summaries", async () => {
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
  const client = configureApiClient({
    origin: "http://localhost:8000",
    fetch: async (path) =>
      new Response(
        JSON.stringify(
          path === "/api/auth/session"
            ? {
                authenticated: true,
                username: "alice",
                role: "user",
                expires_at: "2030-01-01",
              }
            : {
                ok: true,
                runs: [
                  {
                    run_id: "test-training-batch",
                    status: "awaiting_acceptance",
                    result: {
                      census: {
                        n_tested: 1,
                        templates: [],
                        family_ranking: [],
                      },
                      candidates: [
                        {
                          factor_key: "safe-key",
                          expression: "<script>bad()</script>",
                          evaluation: { horizons: { 5: { mean_ic: 0.12 } } },
                        },
                      ],
                    },
                  },
                ],
                factor_library: [],
                expression_templates: [],
              },
        ),
      ),
  });
  try {
    await client.boot();
    const panel =
      await import("../dist/factor-mining-panel.mjs?report-integration");
    const toasts = [];
    panel.initFactorMiningPanel({
      escapeHtml: (value) =>
        String(value)
          .replaceAll("&", "&amp;")
          .replaceAll("<", "&lt;")
          .replaceAll(">", "&gt;"),
      localeText: String,
      setText: (id, value) => {
        dom.window.document.getElementById(id).textContent = value;
      },
      showToast: (value) => toasts.push(value),
      closestHTMLElement: (target) => target,
      loadFactorDefinitions: async () => {},
    });
    await panel.loadFactorMining();
    assert.deepEqual(toasts, []);
    const host = dom.window.document.getElementById("factorMiningRuns");
    assert.match(host.textContent, /模板普查报告/);
    assert.match(host.textContent, /查看训练报告/);
    assert.doesNotMatch(host.textContent, /查看密封验收报告/);
    assert.equal(host.querySelectorAll("script").length, 0);
  } finally {
    dom.window.close();
    for (const [key, value] of saved) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
});
