const assert = require('node:assert/strict');
const test = require('node:test');

test('strategy and global search matching normalize case and whitespace', async () => {
  const module = await import('../dist/strategy-search.mjs');
  assert.equal(module.searchText('  Factor Mining  '), 'factor mining');
  assert.equal(module.localSearchMatches({ title: 'Factor Mining', meta: 'Research', section: 'lab' }, ' FACTOR '), true);
  assert.equal(module.localSearchMatches({ title: 'Risk', meta: 'Controls' }, 'factor'), false);
});

test('strategy labels use the active language and fall back to translated server labels', async () => {
  const module = await import('../dist/strategy-search.mjs');
  const item = { key: 'momentum', label: '趋势策略' };
  assert.equal(module.strategyLabel(item, 'en-US'), 'Dual-horizon trend');
  assert.equal(module.strategyLabel({ key: 'other', label: '趋势策略' }, 'en-US', value => `translated:${value}`), 'translated:趋势策略');
});
