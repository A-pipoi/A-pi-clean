const assert = require('node:assert/strict');
const test = require('node:test');
const { readFileSync } = require('node:fs');
const { JSDOM } = require('jsdom');

test('stock pool count and whitespace do not activate a remove button', async () => {
  const html = readFileSync(require('node:path').join(__dirname, '../dist/index.html'), 'utf8');
  const dom = new JSDOM(html, { url: 'https://localhost/' });
  const names = ['document', 'HTMLElement', 'HTMLInputElement', 'HTMLButtonElement', 'Node'];
  const saved = names.map(name => [name, global[name]]);
  for (const name of names) global[name] = dom.window[name];
  try {
    const picker = await import('../dist/stock-picker.mjs');
    picker.initStockPicker({
      getMaxSymbols: () => 600, getLocale: () => 'zh-CN',
      localeText: text => text, applyLocale() {}, showToast() {},
      escapeHtml: text => String(text).replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('"', '&quot;'),
      closestHTMLElement: (event, selector) => event.target.closest(selector),
    });
    const symbols = ['000001.SZ', '600000.SH'];
    picker.setDefaultStocks(symbols.map(ts_code => ({ ts_code, name: ts_code })), symbols);
    const doc = dom.window.document;
    const host = doc.querySelector('.stock-picker-host');
    assert.equal(host.closest('label'), null);
    assert.equal(host.parentElement.getAttribute('role'), 'group');
    doc.querySelector('.stock-picker-meta').click();
    host.click();
    doc.querySelector('.stock-chip-copy').click();
    assert.equal(doc.querySelector('#symbolInput').value, symbols.join(', '));
    doc.querySelector('[data-remove-stock="0"]').click();
    assert.equal(doc.querySelector('#symbolInput').value, '600000.SH');
    assert.match(doc.querySelector('.stock-picker-meta').textContent, /1\/600/);
  } finally {
    for (const [name, value] of saved) {
      if (value === undefined) delete global[name]; else global[name] = value;
    }
    dom.window.close();
  }
});

test('manual A-share codes normalize market suffixes and reject malformed values', async () => {
  const module = await import('../dist/stock-picker.mjs');
  assert.equal(module.normalizeManualStock('600000')?.ts_code, '600000.SH');
  assert.equal(module.normalizeManualStock('000001.sz')?.ts_code, '000001.SZ');
  assert.equal(module.normalizeManualStock('830001')?.ts_code, '830001.BJ');
  assert.equal(module.normalizeManualStock('60000'), null);
  assert.equal(module.normalizeManualStock('600000.XY'), null);
});

test('paged market results retain existing rows and append only unique stock codes', async () => {
  const module = await import('../dist/stock-picker.mjs');
  const existing = [{ ts_code: '000001.SZ', name: '平安银行' }];
  const merged = module.appendUniqueStockRows(existing, [
    { ts_code: '000001.SZ', name: '重复行' },
    { ts_code: '600000.SH', name: '浦发银行' },
    { name: '缺少代码' },
    { ts_code: '600000.SH', name: '同页重复' },
  ]);
  assert.deepEqual(merged.map(item => item.ts_code), ['000001.SZ', '600000.SH']);
  assert.equal(existing.length, 1);
});
