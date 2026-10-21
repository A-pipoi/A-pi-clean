import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolveTheme } from '../dist/theme.mjs';
import { selectStableMarketTickerQuotes } from '../dist/market-ticker.mjs';

test('explicit theme overrides system, and system preference is respected',()=>{
  assert.equal(resolveTheme('system',true),'dark');
  assert.equal(resolveTheme('light',true),'light');
  assert.equal(resolveTheme('dark',false),'dark');
});
test('ticker deduplicates codes, pins priority and has no recurring live region',()=>{
  const quotes=[{ts_code:'000300.SH'},{ts_code:'399006.SZ'},{ts_code:'000300.SH'}];
  assert.deepEqual(selectStableMarketTickerQuotes(quotes).map(q=>q.ts_code),['399006.SZ','000300.SH']);
  const html=readFileSync(new URL('../dist/index.html',import.meta.url),'utf8');
  assert.match(html,/id="marketTicker"[^>]*aria-live="off"/);
  assert.doesNotMatch(readFileSync(new URL('../dist/market-ticker.mjs',import.meta.url),'utf8'),/Math\.random/);
});
test('workspaces never fabricate successful 404 responses',()=>{
  const code=readFileSync(new URL('../dist/workspace-controller.mjs',import.meta.url),'utf8');
  assert.doesNotMatch(code,/a_pi_health_fallback|response.status === 404/);
});
test('deep links exclude prototype keys and a keyboard pause is available',()=>{
  const code=readFileSync(new URL('../dist/app-shell.mjs',import.meta.url),'utf8');
  assert.match(code,/Object\.hasOwn\(deps.sectionNames, id\)/);
  assert.doesNotMatch(code,/id in deps.sectionNames/);
  const html=readFileSync(new URL('../dist/index.html',import.meta.url),'utf8');
  assert.match(html,/id="marketTickerPause"[^>]*aria-pressed="false"/);
});
