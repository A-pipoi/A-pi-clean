const assert = require('node:assert/strict');
const test = require('node:test');

const tickerModule = import('../dist/market-ticker.mjs');

test('market ticker selection is deterministic, unique and covers every available symbol', async () => {
  const { selectStableMarketTickerQuotes } = await tickerModule;
  const quotes = Array.from({ length: 12 }, (_, index) => ({ ts_code: `IDX${index}` }));
  const originalRandom = Math.random;
  let first;
  let second;
  try {
    Math.random = () => 0;
    first = selectStableMarketTickerQuotes(quotes);
    second = selectStableMarketTickerQuotes([...quotes].reverse());
  } finally {
    Math.random = originalRandom;
  }
  assert.equal(first.length, 12);
  assert.equal(new Set(first.map((quote) => quote.ts_code)).size, 12);
  assert.deepEqual(second, first);
});
