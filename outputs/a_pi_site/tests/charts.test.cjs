const assert = require('node:assert/strict');
const test = require('node:test');

test('equity curves use the first capital value as their index base, falling back to one at zero', async () => {
  const module = await import('../dist/charts.mjs');
  assert.deepEqual(module.normalizeEquityValues([100, 105, 95]), [1, 1.05, 0.95]);
  assert.deepEqual(module.normalizeEquityValues([0, 5, 10]), [0, 5, 10]);
  assert.deepEqual(module.normalizeEquityValues([]), []);
});
