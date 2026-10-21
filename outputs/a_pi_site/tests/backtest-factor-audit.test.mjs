import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { JSDOM } from "jsdom";
import { createBacktestFactorAudit } from "../dist/backtest-factor-audit.mjs";
import { buildBacktestPreset } from "../dist/backtest-research-controls.mjs";

test("audited presets stay fixed even before the remote catalog loads", () => {
  for (const strategy of ["baseline_price_audited", "baseline_full_audited"]) {
    const payload = buildBacktestPreset({
      doc: {
        getElementById: (id) =>
          id === "strategySelect" ? { value: strategy } : null,
      },
      strategyCatalog: [],
      researchTopN: 10,
    });
    assert.equal(payload.lookback, 20);
    assert.equal(payload.top_n, 5);
  }
});

test("factor diagnostics belong to current run, escape values and distinguish missing evidence", async () => {
  const html = await readFile(
    new URL("../dist/index.html", import.meta.url),
    "utf8",
  );
  const dom = new JSDOM(html);
  const previous = globalThis.document;
  globalThis.document = dom.window.document;
  try {
    const panel = document.getElementById("backtestFactorAudit");
    const host = document.getElementById("backtestFactorAuditBody");
    assert.equal(panel.open, false);
    const ui = createBacktestFactorAudit();
    ui.render({
      strategy: "price-test",
      factor_pipeline: {
        coverage_policy: "complete",
        retention_rank: 8,
        rebalance_deadband: 0.02,
        cash_reserve: 0.02,
        participation: {
          status: "available",
          evaluations: 5,
          latest: {
            day: "2025-02-09",
            eligible_symbols: 90,
            requested_symbols: 100,
          },
          factors: [
            {
              key: "a",
              name: "<img src=x>",
              covered: 90,
              requested: 100,
              overall_coverage: 0.8,
              used: 90,
              missing_reason: "missing_point_in_time_financial",
            },
          ],
          correlations: [
            {
              left: "a",
              right: "b",
              mean_rank_correlation: -0.9,
              mean_absolute_correlation: 0.9,
              evaluations: 2,
              redundancy_warning: true,
            },
          ],
          correlation_basis: "fixture-basis",
        },
      },
      training_weight_comparison: {
        training_start: "2025-01-01",
        training_end: "2025-02-09",
        note: "不自动选优",
        rows: [
          {
            profile: "equal_factor",
            annualized_return: null,
            sharpe: null,
            max_drawdown: null,
            turnover: null,
            total_cost: 0,
            status: "insufficient_factor_coverage",
          },
        ],
      },
    });
    assert.equal(host.querySelectorAll("table").length, 3);
    assert.equal(host.querySelector("img"), null);
    assert.match(host.textContent, /缺少当时可用的财务证据/);
    assert.match(host.textContent, /高度相关/);
    assert.match(host.textContent, /因子覆盖不足/);
    assert.match(host.textContent, /全程覆盖|80.00%/);
    assert.match(host.textContent, /共4次/);
    assert.equal(host.querySelectorAll('th[scope="col"]').length, 18);
    ui.render({ strategy: "old-result" });
    assert.match(host.textContent, /未保存因子参与诊断/);
    assert.equal(host.querySelectorAll("table").length, 0);
    assert.doesNotMatch(host.textContent, /price-test|fixture-basis/);
  } finally {
    if (previous === undefined) delete globalThis.document;
    else globalThis.document = previous;
    dom.window.close();
  }
});
