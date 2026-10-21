const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const controlsModule = import('../dist/backtest-research-controls.mjs');

test('research top-N accepts only integers from one to ten', async () => {
  const { normalizeResearchTopN } = await controlsModule;
  assert.equal(normalizeResearchTopN('1'), 1);
  assert.equal(normalizeResearchTopN('10'), 10);
  assert.equal(normalizeResearchTopN(5), 5);
  assert.equal(normalizeResearchTopN(0), 5);
  assert.equal(normalizeResearchTopN(11), 5);
  assert.equal(normalizeResearchTopN('5.5'), 5);
});

test('research control installer safely no-ops without a document', async () => {
  const { installBacktestResearchControls } = await controlsModule;
  assert.equal(installBacktestResearchControls(null), null);
});

test('research presets accept the selected breadth and rebalance settings', async () => {
  const { buildBacktestPreset } = await controlsModule;
  const fields = {
    strategySelect: { value: 'momentum' },
    dataSourceSelect: { value: 'tushare' },
    symbolInput: { value: '600036.SH, 000001.SZ,600036.SH' },
    backtestUniverseMode: { value: 'fixed' },
    backtestStart: { value: '2024-01-02' },
    backtestEnd: { value: '2026-09-29' },
    rebalanceSelect: { value: 'monthly' },
    costSelect: { value: 'standard' },
  };
  const payload = buildBacktestPreset({
    doc: { getElementById: (id) => fields[id] || null },
    strategyCatalog: [{ key: 'momentum', lookback: 12, top_n: 5 }],
    researchTopN: 10,
  });
  assert.equal(payload.strategy, 'momentum');
  // Catalog presets ignore #backtestTopN / researchTopN overrides.
  assert.equal(payload.top_n, 5);
  assert.equal(payload.lookback, 12);
  assert.equal(payload.rebalance, 'monthly');
  assert.deepEqual(payload.symbols, ['600036.SH', '000001.SZ']);
});

test('paper presets ignore the research-only breadth override', async () => {
  const { buildBacktestPreset } = await controlsModule;
  const payload = buildBacktestPreset({
    doc: { getElementById: () => null },
    strategyCatalog: [{ key: 'baseline', lookback: 20, top_n: 5 }],
    researchTopN: 10,
    researchOnly: false,
  });
  assert.equal(payload.top_n, 5);
});

test('risk limits are normalized through the module API and reject missing required values', async () => {
  const { APiRiskView } = await import('../dist/risk-view.js');
  const normalizeLimits = APiRiskView.normalizeLimits;
  const valid = normalizeLimits({
    max_single_weight: '0.2',
    max_industry_weight: 0.5,
    max_gross_leverage: 1,
    max_drawdown: 0.3,
    max_var_95: null,
  });
  assert.equal(valid.max_single_weight, 0.2);
  assert.equal(valid.max_var_95, null);
  assert.equal(normalizeLimits({ max_single_weight: 0.2, max_drawdown: null }), null);
});

test('fixed catalog strategies ignore researchTopN overrides even without catalog entry', async () => {
  const { buildBacktestPreset } = await controlsModule;
  const payload = buildBacktestPreset({
    doc: {
      getElementById: (id) =>
        id === 'strategySelect' ? { value: 'lightgbm' } : id === 'backtestTopN' ? { value: '9' } : null,
    },
    strategyCatalog: [],
    researchTopN: 9,
  });
  assert.equal(payload.lookback, 60);
  assert.equal(payload.top_n, 5);
});
