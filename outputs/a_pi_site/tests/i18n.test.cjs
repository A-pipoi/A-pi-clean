const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

function loadLocaleRuntime(dictionary) {
  const window = {};
  const document = {
    body: {},
    documentElement: { lang: 'zh-CN' },
    title: '',
    getElementById: () => null,
    createTreeWalker: () => ({ nextNode: () => null }),
  };
  class MutationObserver {
    observe() {}
  }
  const context = {
    window,
    document,
    localStorage: { getItem: () => null, setItem: () => {} },
    Element: class Element {},
    Node: { TEXT_NODE: 3, ELEMENT_NODE: 1 },
    NodeFilter: { SHOW_TEXT: 4 },
    MutationObserver,
  };
  const source = fs.readFileSync(path.join(__dirname, '../dist/i18n.js'), 'utf8');
  vm.runInNewContext(source.replace('export const APiLocaleRuntime', 'const APiLocaleRuntime') + '\nwindow.APiLocaleRuntime = APiLocaleRuntime;', context);
  return window.APiLocaleRuntime.create(dictionary, 'test-locale');
}

test('reverse canonicalization only matches a complete unique translation', () => {
  const runtime = loadLocaleRuntime({
    'en-US': { '上证指数': 'Shanghai Composite Index', '实时指数': 'Live index' },
    'ja-JP': { '上证指数': '上海総合指数', '实时指数': '指数' },
  });

  assert.equal(runtime.canonicalize('指数'), '指数');
  assert.equal(runtime.canonicalize('上证指数'), '上证指数');
  assert.equal(runtime.canonicalize('上海総合指数'), '上证指数');
});

test('ambiguous reverse translations are never guessed', () => {
  const runtime = loadLocaleRuntime({
    'en-US': { '甲词': 'Shared label', '乙词': 'Shared label', '丙词': 'Shared label' },
  });

  assert.equal(runtime.canonicalize('Shared label'), 'Shared label');
});

test('partially covered phrases fall back intact instead of mixing languages', () => {
  const runtime = loadLocaleRuntime({
    'en-US': { '策略': 'Strategy', '页面': 'page', '请查看策略页面': 'Open the strategy page' },
  });
  runtime.apply('en-US');

  assert.equal(runtime.translate('请查看策略页面外的内容'), '请查看策略页面外的内容');
  assert.equal(runtime.translate('请查看策略页面'), 'Open the strategy page');
});

test('locale changes keep source phrases intact across Chinese, English, and Japanese', () => {
  const runtime = loadLocaleRuntime({
    'en-US': { '上证指数': 'Shanghai Composite Index', '实时指数': 'Live index' },
    'ja-JP': { '上证指数': '上海総合指数', '实时指数': 'リアルタイム指数' },
  });

  runtime.apply('en-US');
  assert.equal(runtime.translate('上证指数'), 'Shanghai Composite Index');
  runtime.apply('ja-JP');
  assert.equal(runtime.translate('上证指数'), '上海総合指数');
  assert.equal(runtime.canonicalize('上海総合指数'), '上证指数');
  runtime.apply('zh-CN');
  assert.equal(runtime.translate('上证指数'), '上证指数');
});
