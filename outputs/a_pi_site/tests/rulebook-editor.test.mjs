import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';
import { configureApiClient } from '../dist/api-client.mjs';
import { createRulebookEditor, selectedRulebookVersion } from '../dist/rulebook-editor.mjs';

test('rulebook visibility safely closes the details element when leaving the strategy', () => {
  const dom = new JSDOM('<html lang="zh-CN"><body><select id="strategySelect"><option value="rulebook">rules</option><option value="baseline">baseline</option></select><details id="rulebookEditor" open></details></body></html>');
  const previousDocument = globalThis.document;
  const previousObserver = globalThis.MutationObserver;
  globalThis.document = dom.window.document;
  globalThis.MutationObserver = dom.window.MutationObserver;
  try {
    createRulebookEditor();
    const host = document.getElementById('rulebookEditor');
    const strategy = document.getElementById('strategySelect');
    assert.equal(host.hidden, false);
    assert.equal(host.open, true);
    strategy.value = 'baseline';
    strategy.dispatchEvent(new dom.window.Event('change'));
    assert.equal(host.hidden, true);
    assert.equal(host.open, false);
    strategy.value = 'rulebook';
    strategy.dispatchEvent(new dom.window.Event('change'));
    assert.equal(host.hidden, false);
    assert.equal(host.open, false);
    host.remove();
    assert.doesNotThrow(() => createRulebookEditor());
    const declarations = readFileSync(new URL('../types/dom-elements.generated.d.ts', import.meta.url), 'utf8');
    assert.match(declarations, /rulebookEditor["']?: HTMLDetailsElement/);
  } finally {
    if (previousDocument === undefined) delete globalThis.document;
    else globalThis.document = previousDocument;
    if (previousObserver === undefined) delete globalThis.MutationObserver;
    else globalThis.MutationObserver = previousObserver;
    dom.window.close();
  }
});

test('saved rule versions are required; malformed drafts survive reload and switching', async () => {
  const dom = new JSDOM('<html lang="zh-CN"><body><details id="rulebookEditor"></details></body></html>');
  globalThis.document = dom.window.document;
  globalThis.MutationObserver = dom.window.MutationObserver;
  const recipe = {name:'<img src=x onerror=alert(1)>',schema:'daily-rulebook-v1'};
  let queries = 0;
  const client = configureApiClient({origin:'http://localhost:8000',fetch:async url => {
    if (url === '/api/auth/session') return new Response(JSON.stringify({authenticated:true,username:'alice',role:'user',expires_at:'2030-01-01'}));
    queries++;
    return new Response(JSON.stringify({ok:true,recipes:[{version:'v1',recipe},{version:'v2',recipe:{...recipe,name:'other'}}],template:recipe}));
  }});
  await client.boot();
  const editor = createRulebookEditor();
  await editor.refresh();
  assert.equal(selectedRulebookVersion('rulebook'), 'v1');
  assert.equal(document.querySelector('img'), null);
  const input = document.querySelector('textarea');
  input.value = '{broken JSON';
  assert.throws(() => selectedRulebookVersion('rulebook'));
  document.querySelectorAll('button')[1].click();
  const select = document.querySelector('select');
  select.value = 'v2'; select.dispatchEvent(new dom.window.Event('change'));
  await editor.refresh();
  assert.equal(select.value, 'v1');
  assert.equal(input.value, '{broken JSON');
  assert.equal(queries, 1);
  assert.equal(selectedRulebookVersion('baseline'), '');
  dom.window.close(); delete globalThis.document; delete globalThis.MutationObserver;
});
