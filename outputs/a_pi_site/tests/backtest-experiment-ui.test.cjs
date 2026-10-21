const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { JSDOM } = require('jsdom');

const root = path.join(__dirname, '..');
const html = readFileSync(path.join(root, 'dist', 'index.html'), 'utf8');

test('backtest config panel follows data → strategy → universe → run → results order', () => {
  const start = html.indexOf('id="backtest"');
  const next = html.indexOf('class="page-section"', start + 10);
  const chunk = html.slice(start, next);
  const order = [
    'backtestDataSection',
    'featureStoreMount',
    'backtestStrategySection',
    'rulebookEditor',
    'backtestStrategyParams',
    'backtestUniverseSection',
    'backtestRunSection',
    'configRunButton',
    'sensitivityButton',
    'backtestProgress',
    'backtestProgressKind',
    'backtestResultsPanel',
    'backtestResultEmpty',
    'backtestResultBody',
  ].map((id) => chunk.indexOf(`id="${id}"`));
  assert.ok(order.every((i) => i >= 0), 'missing structural ids');
  for (let i = 1; i < order.length; i += 1) {
    assert.ok(order[i] > order[i - 1], `order broken at ${i}`);
  }
  assert.match(chunk, /config-sensitivity-link/);
  assert.doesNotMatch(chunk, /id="sensitivityButton"[^>]*secondary-button full/);
  assert.match(chunk, /id="rulebookEditor"[^>]*hidden/);
  assert.match(chunk, /尚未运行/);
  assert.doesNotMatch(chunk, /id="backtestMonthly">等待真实回测/);
  assert.match(chunk, /config-section__grid/);
  const document = new JSDOM(html).window.document;
  assert.equal(document.querySelector('#backtestRunSection h3').textContent, '运行与进度');
  assert.equal(document.querySelector('#backtestResultsPanel h2').textContent, '样本外摘要');
  const css = readFileSync(path.join(root, 'dist', 'workbench-usability.css'), 'utf8');
  const styles = readFileSync(path.join(root, 'dist', 'styles.css'), 'utf8');
  assert.match(css, /backtest flow layout 2026-10-15-ui6/);
  assert.match(css, /backtest content width align 2026-10-15-ui8/);
  assert.match(css, /--backtest-content-max:\s*none/);
  assert.match(css, /#backtest > \.page-heading/);
  assert.match(css, /#backtest > \.governance-section/);
  assert.match(css, /config-section__grid/);
  assert.doesNotMatch(css, /#backtest \.backtest-layout \{[\s\S]*max-width:\s*72rem/);
  assert.doesNotMatch(css, /backtest stack layout 2026-10-15-ui3/);
  const polish = readFileSync(path.join(root, 'dist', 'workbench-polish.mjs'), 'utf8');
  assert.match(polish, /sectioned/);
  assert.match(styles, /\.backtest-layout \{[\s\S]*grid-template-columns:\s*minmax\(0,\s*1fr\)/);
  assert.doesNotMatch(styles, /\.backtest-layout,\s*\.risk-layout \{[\s\S]*330px/);
});

test('setExperimentProgress labels kind and drives shared host', async () => {
  const dom = new JSDOM(`<!doctype html><html lang="zh-CN"><body>
    <div id="backtestProgress" hidden>
      <span id="backtestProgressKind"></span>
      <span id="backtestProgressMessage"></span>
      <strong id="backtestProgressValue"></strong>
      <span id="backtestProgressBar"></span>
      <small id="backtestProgressMeta"></small>
    </div>
  </body></html>`, { url: 'https://127.0.0.1:8000/' });
  const { window } = dom;
  globalThis.window = window;
  globalThis.document = window.document;
  const mod = await import('../dist/experiment-progress.mjs');
  mod.setExperimentProgress({
    kind: 'feature_warmup',
    message: 'warming',
    value: 42,
    meta: 'batch 1',
  });
  const host = window.document.getElementById('backtestProgress');
  assert.equal(host.hidden, false);
  assert.equal(host.dataset.kind, 'feature_warmup');
  assert.equal(window.document.getElementById('backtestProgressKind').textContent, '特征预热');
  assert.equal(window.document.getElementById('backtestProgressValue').textContent, '42%');
  assert.equal(window.document.getElementById('backtestProgressBar').style.width, '42%');
  mod.setExperimentProgress({ kind: 'backtest', message: 'running', value: 15 });
  assert.equal(window.document.getElementById('backtestProgressKind').textContent, '回测任务');
  mod.clearExperimentProgress({ onlyIfKind: 'feature_warmup' });
  assert.equal(host.hidden, false, 'should not clear other kind');
  mod.clearExperimentProgress({ onlyIfKind: 'backtest' });
  assert.equal(host.hidden, true);
  window.document.documentElement.lang = 'en-US';
  for (const kind of ['unknown_kind', 'toString', '__proto__']) {
    mod.setExperimentProgress({ kind, value: 10 });
    assert.equal(window.document.getElementById('backtestProgressKind').textContent, 'Backtest');
  }
  delete globalThis.window;
  delete globalThis.document;
});

test('feature store panel boots into featureStoreMount when present', async () => {
  const dom = new JSDOM(`<!doctype html><html lang="zh-CN"><body>
    <div id="featureStoreMount"></div>
    <div class="sensitivity-result"></div>
    <div id="backtestProgress" hidden>
      <span id="backtestProgressKind"></span>
      <span id="backtestProgressMessage"></span>
      <strong id="backtestProgressValue"></strong>
      <span id="backtestProgressBar"></span>
      <small id="backtestProgressMeta"></small>
    </div>
  </body></html>`, {
    url: 'https://127.0.0.1:8000/',
    runScripts: 'outside-only',
  });
  const { window } = dom;
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.MutationObserver = window.MutationObserver;
  window.fetch = async () => ({ ok: false, status: 401, json: async () => ({}) });
  await import(`../dist/feature-store-panel.mjs?ts=${Date.now()}`);
  await new Promise((r) => setTimeout(r, 20));
  const panel = window.document.getElementById('featureStorePanel');
  assert.ok(panel, 'panel should mount');
  assert.equal(panel.parentElement?.id, 'featureStoreMount');
  assert.equal(window.document.querySelectorAll('#featureStorePanel').length, 1);
  delete globalThis.window;
  delete globalThis.document;
  delete globalThis.MutationObserver;
});
