import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { JSDOM } from "jsdom";
import { configureApiClient } from "../dist/api-client.mjs";

async function mounted(scenario, exercise) {
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
  const posts = [];
  let reads = 0;
  const version = "a".repeat(64);
  const snapshot = {
    ok: true,
    runs: [],
    factor_library: [],
    expression_templates: [],
    composition_factors: Array.from({ length: 9 }, (_, i) => ({
      key: `factor${i}`,
      name: i === 0 ? "<img src=x onerror=bad()>" : `测试因子 ${i}`,
    })),
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
        const body = JSON.parse(options.body);
        posts.push({ path, body, headers: options.headers });
        if (scenario === "ambiguous") throw new TypeError("Failed to fetch");
        if (scenario === "busy")
          return new Response(
            JSON.stringify({ ok: false, error: "operation_already_active" }),
            { status: 429, headers: { "Retry-After": "180" } },
          );
        if (path.endsWith("/import"))
          return new Response(
            JSON.stringify({
              ok: true,
              model_id: "model1",
              version,
              status: "research_only",
              enabled: false,
            }),
          );
        return new Response(
          JSON.stringify({
            ...snapshot,
            run: { run_id: "test-run", n_tested: 1 },
          }),
        );
      }
      if (path === "/api/mining/external-deep") {
        reads++;
        return new Response(
          JSON.stringify({
            ok: true,
            available: [
              {
                model_id: "model1",
                version,
                status: "research_only",
                enabled: false,
              },
            ],
            imported: posts.length ? [{ version }] : [],
          }),
        );
      }
      return new Response(JSON.stringify(snapshot));
    },
  });
  try {
    await client.boot();
    const panel = await import(
      `../dist/factor-mining-panel.mjs?workflow=${scenario}`
    );
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
      showToast: () => {},
      closestHTMLElement: (target) => target,
      loadFactorDefinitions: async () => {},
    });
    await panel.loadFactorMining();
    await exercise({
      doc: dom.window.document,
      panel,
      posts,
      timers,
      get reads() {
        return reads;
      },
      version,
    });
  } finally {
    dom.window.close();
    for (const [key, value] of saved) {
      if (value === undefined) delete globalThis[key];
      else globalThis[key] = value;
    }
  }
}

async function settled() {
  for (let i = 0; i < 20; i++)
    await new Promise((resolve) => setImmediate(resolve));
}

test("composition validates selection, preserves checks and sends one explicit ICIR request", async () =>
  mounted("compose", async ({ doc, panel, posts }) => {
    const button = doc.getElementById("factorComposeButton");
    const inputs = doc.querySelectorAll("#factorComposeFactors input");
    assert.equal(button.disabled, true);
    assert.equal(doc.querySelector("#factorComposeFactors img"), null);
    for (const input of [inputs[0], inputs[1]]) {
      input.checked = true;
      input.dispatchEvent(
        new doc.defaultView.Event("change", { bubbles: true }),
      );
    }
    assert.equal(button.disabled, false);
    assert.match(
      doc.getElementById("factorComposeStatus").textContent,
      /预计消耗 3 次/,
    );
    await panel.loadFactorMining();
    assert.equal(
      doc.querySelectorAll("#factorComposeFactors input:checked").length,
      2,
    );
    doc.getElementById("factorComposeWeighting").value = "icir";
    button.click();
    button.click();
    await settled();
    assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].body, {
      mode: "compose",
      factor_keys: ["factor0", "factor1"],
      weighting: "icir",
    });
    assert.equal(new Headers(posts[0].headers).get("X-A-Pi-Request"), "1");
    for (const input of doc.querySelectorAll("#factorComposeFactors input")) {
      input.checked = true;
      input.dispatchEvent(
        new doc.defaultView.Event("change", { bubbles: true }),
      );
    }
    assert.equal(button.disabled, true);
    assert.equal(
      doc.getElementById("factorComposeGroup").getAttribute("aria-invalid"),
      "true",
    );
    assert.equal(posts.length, 1);
  }));

test("deep candidate import remains research-only and sends immutable ID/version only", async () =>
  mounted("import", async ({ doc, posts, version }) => {
    const button = doc.getElementById("factorDeepImportButton");
    assert.equal(button.disabled, true);
    const status = doc.getElementById("factorDeepStatus");
    assert.ok(status, "深度候选必须提供可见的状态反馈元素");
    assert.equal(status.getAttribute("role"), "status");
    assert.equal(status.getAttribute("aria-live"), "polite");
    doc.getElementById("factorDeepRefreshButton").click();
    assert.match(status.textContent, /正在读取/);
    await settled();
    assert.equal(doc.getElementById("factorDeepRefreshButton").disabled, false);
    const select = doc.getElementById("factorDeepCandidate");
    select.value = "model1";
    select.dispatchEvent(new doc.defaultView.Event("change"));
    button.click();
    button.click();
    await settled();
    assert.equal(posts.length, 1);
    assert.deepEqual(posts[0].body, { model_id: "model1", version });
    assert.match(
      doc.getElementById("factorDeepStatus").textContent,
      /仍未启用/,
    );
    assert.match(doc.getElementById("factorDeepStatus").textContent, /未合并/);
  }));

for (const scenario of ["ambiguous", "busy"]) {
  test(`deep import ${scenario} waits for read-only reconciliation, never automatically reposts`, async () =>
    mounted(scenario, async (state) => {
      const { doc, posts } = state;
      doc.getElementById("factorDeepRefreshButton").click();
      await settled();
      const select = doc.getElementById("factorDeepCandidate");
      select.value = "model1";
      select.dispatchEvent(new doc.defaultView.Event("change"));
      const button = doc.getElementById("factorDeepImportButton");
      button.click();
      await settled();
      assert.equal(posts.length, 1);
      assert.equal(button.disabled, true);
      assert.match(
        doc.getElementById("factorDeepStatus").textContent,
        /不会自动重复导入/,
      );
      button.click();
      await settled();
      assert.equal(posts.length, 1, "结果未确认时不得重复提交导入");
      assert.equal(
        doc.getElementById("factorDeepRefreshButton").disabled,
        false,
      );
      doc.getElementById("factorDeepRefreshButton").click();
      await settled();
      assert.equal(state.reads, 2);
      assert.equal(posts.length, 1);
      assert.equal(button.disabled, false);
    }));
}
