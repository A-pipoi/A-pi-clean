import test from "node:test";
import assert from "node:assert/strict";
import { summarizeHistoricalCoverage } from "../dist/data-extension-panel.mjs";

test("historical coverage counts verified days and quarter-symbol observations", () => {
  const result = summarizeHistoricalCoverage({
    full_history_acceptance: {
      sessions: 486,
      coverage: Array.from({ length: 486 }, (_, i) => ({ date: String(i), complete: i < 476 })),
    },
    fund_historical_layers: { periods: [12,24,7,5,14,22,7,4,2,7,21,12].map((n) => ({ missing_price_symbols: Array(n).fill("same-symbol") })) },
  });
  assert.deepEqual(result, { sessions: 486, passedDays: 476, missingFundObservations: 137 });
});

test("missing history evidence is unknown, not zero missing observations", () => {
  assert.deepEqual(summarizeHistoricalCoverage({}), { sessions: null, passedDays: null, missingFundObservations: null });
  assert.equal(summarizeHistoricalCoverage({ fund_historical_layers: { periods: [{}] } }).missingFundObservations, null);
});

test("incomplete or duplicate date reports cannot claim complete coverage", () => {
  for (const coverage of [[{ date: "a", complete: true }], [{ date: "a", complete: true }, { date: "a", complete: true }]]) {
    assert.equal(summarizeHistoricalCoverage({ full_history_acceptance: { sessions: 2, complete: true, coverage } }).passedDays, null);
  }
});
