import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

const read = (name) => readFileSync(new URL(`../dist/${name}`, import.meta.url), 'utf8');

test('typography is local and shares one family across text, numeric and code roles', () => {
  const styles = read('styles.css');
  const typography = read('typography.css');
  assert.doesNotMatch(styles, /fonts\.googleapis|DM Mono|Manrope/);
  assert.doesNotMatch(styles, /body :where\(:not\(svg\)/);
  assert.match(read('design-tokens.css'), /--font-ui:\s*"Microsoft YaHei UI"/);
  assert.match(read('design-tokens.css'), /--font-num:\s*var\(--font-ui\)/);
  assert.match(read('design-tokens.css'), /--font-code:\s*var\(--font-num\)/);
  assert.match(typography, /html,[\s\S]*?summary\s*\{\s*font-family: var\(--font-ui\)/);
  assert.match(typography, /font-family: var\(--font-num\)/);
  assert.match(typography, /tabular-nums/);
  assert.ok(read('index.html').indexOf('typography.css?') > read('index.html').indexOf('account-security.css'));
  assert.match(read('chart-engine.mjs'), /getComputedStyle\(document.body\)\.fontFamily/);
});
