import assert from "node:assert/strict";
import test from "node:test";
import {
  portfolioMarkup,
  arenaReportMarkup,
} from "../dist/portfolio-arena.mjs";
import { factorEvaluationReportMarkup } from "../dist/factor-mining-panel.mjs";

test("sealed report explicitly renders conflicting IC and portfolio decisions", () => {
  const html = factorEvaluationReportMarkup({
    evaluation: { horizons: {} },
    ic_passed: true,
    portfolio_passed: false,
    metric_conflict: true,
    decision_note: "<script>bad()</script>",
  });
  assert.match(html, /两种口径存在冲突/);
  assert.match(html, /IC 校正：通过/);
  assert.match(html, /训练组合门槛：未通过/);
  assert.doesNotMatch(html, /<script>/);
});

test("portfolio report displays gross and all net scenarios without inventing missing values", () => {
  const html = portfolioMarkup({
    10: {
      status: "available",
      gross: { annual_sharpe: 1.25 },
      net: [0.05, 0.1, 0.2].map((x) => ({
        daily_one_way_turnover: x,
        metrics: { annual_sharpe: null },
      })),
      dsr: { probability: 0.4, n_tested: 123 },
      psr: { probability: 0.95 },
    },
  });
  assert.match(html, /毛收益/);
  for (const rate of ["5.0%", "10.0%", "20.0%"]) assert.ok(html.includes(rate));
  assert.match(html, /N_tested 123/);
  assert.match(html, /95.0%/);
  assert.match(html, /—/);
  assert.doesNotMatch(html, /Infinity|NaN/);
});

test("arena report escapes proposer names and never claims a training-only winner", () => {
  const html = arenaReportMarkup({
    status: "running",
    arena_id: "a",
    global_n_tested: 7,
    ranking_basis: "training_provisional",
    participants: [
      {
        proposer_tag: "<img src=x onerror=bad()>",
        evaluations_used: 1,
        n_tested: 1,
        budget: 2,
      },
    ],
  });
  assert.ok(html.includes("&lt;img"));
  assert.ok(html.includes("不能据此判定最终胜负"));
  assert.doesNotMatch(html, /<img|NaN|Infinity/);
});

test("factor details place IC decay and daily portfolio in one responsive layout", () => {
  const html = factorEvaluationReportMarkup(
    {
      evaluation: {
        horizons: {},
        ic_decay_curve: { points: [], status: "insufficient_data" },
        portfolio: { 10: { status: "insufficient_data" } },
      },
    },
    (x) => String(x),
  );
  assert.match(html, /portfolio-ic-layout/);
  assert.match(html, /IC 衰减/);
  assert.match(html, /日频多空组合/);
});
