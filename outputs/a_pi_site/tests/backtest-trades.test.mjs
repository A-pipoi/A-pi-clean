import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { JSDOM } from "jsdom";
import { createBacktestTrades } from "../dist/backtest-trades.mjs";

test("backtest fills filter, page, escape text and replace stale results", async () => {
  const html = await readFile(new URL("../dist/index.html", import.meta.url), "utf8");
  const dom = new JSDOM(html);
  const keys = ["document", "HTMLSelectElement", "HTMLButtonElement"];
  const previous = keys.map((key) => globalThis[key]);
  for (const key of keys) globalThis[key] = dom.window[key];
  try {
    const trades = createBacktestTrades();
    const host = document.getElementById("backtestTrades");
    const filter = host.querySelector("select");
    trades.render({ strategy: "fixture", job_id: "test-one", fills_detail:
      Array.from({ length: 25 }, (_, i) => ({ date: "2024-01-03", symbol: i === 0 ? "<img src=x onerror=alert(1)>" : `TEST-${i}`, side: i < 23 ? "BUY" : "SELL", quantity: 100 })) });
    assert.equal(host.querySelectorAll("tbody tr").length, 20);
    assert.equal(host.querySelector("tbody img"), null);
    host.querySelector('[data-page="next"]').click();
    assert.equal(host.querySelectorAll("tbody tr").length, 5);
    filter.value = "SELL";
    filter.dispatchEvent(new dom.window.Event("change"));
    assert.equal(host.querySelectorAll("tbody tr").length, 2);
    assert.match(host.querySelector("tbody").textContent, /卖出/);
    assert.equal(host.querySelector('[data-page="previous"]').disabled, true);
    trades.render({ strategy: "fixture-two", fills: 6 });
    assert.match(host.textContent, /未保存成交明细/);
    assert.doesNotMatch(host.querySelector("tbody").textContent, /TEST-/);
    assert.equal(filter.disabled, true);
    trades.render({ fills: 0, fills_detail: [] });
    assert.match(host.querySelector("tbody").textContent, /本次回测没有成交/);
    assert.equal(filter.disabled, false);
  } finally {
    keys.forEach((key, i) => { if (previous[i] === undefined) delete globalThis[key]; else globalThis[key] = previous[i]; });
    dom.window.close();
  }
});
