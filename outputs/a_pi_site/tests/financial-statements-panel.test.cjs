const assert = require('node:assert/strict');
const test = require('node:test');

test('financial statement values use readable magnitudes and locale-aware small numbers', async () => {
  const { statementNumber } = await import('../dist/financial-statements-panel.mjs');
  assert.equal(statementNumber(123456789), '1.23 亿');
  assert.equal(statementNumber(12345), '1.23 万');
  assert.equal(statementNumber(1234.5), '1,234.5');
  assert.equal(statementNumber('not-a-number'), '—');
});
