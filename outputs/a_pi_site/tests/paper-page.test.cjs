const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const test = require('node:test');

test('paper order controls require data approval, limits and freshness', async () => {
  const { paperTradingBlocked, paperStatusLabel } = await import('../dist/paper-page.mjs');
  assert.equal(paperTradingBlocked({ paper_trading_allowed: true, data_quality: { limits_available: true }, data_freshness: { fresh: true } }), false);
  assert.equal(paperTradingBlocked({ paper_trading_allowed: true, data_quality: { limits_available: false }, data_freshness: { fresh: true } }), true);
  assert.equal(paperTradingBlocked({ paper_trading_allowed: true, data_quality: { limits_available: true }, data_freshness: { fresh: false } }), true);
  assert.equal(paperTradingBlocked({ paper_trading_allowed: false }, null, null), true);
  assert.equal(paperStatusLabel('REJECTED'), '已拒绝');
});

test('positive home paper return uses the red profit color', () => {
  const root = join(__dirname, '..', 'dist');
  const page = readFileSync(join(root, 'paper-page.mjs'), 'utf8');
  const css = readFileSync(join(root, 'styles.css'), 'utf8');
  assert.match(page, /homePaperReturn\?\.classList\.toggle\([\s\S]*?"positive"[\s\S]*?Number\(paper\.return_pct\)\s*>\s*0/);
  assert.match(css, /\.side-account-hero \.datum-note\.positive\s*\{\s*color:\s*var\(--up\);\s*\}/);
  const tokens = readFileSync(join(root, 'design-tokens.css'), 'utf8');
  assert.match(tokens, /--up:\s*#d9363e/);
});
