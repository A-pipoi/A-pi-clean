const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

test('standalone locale dictionary exposes every supported locale and key navigation labels', () => {
  const context = { window: {} };
  const source = readFileSync(path.join(__dirname, '..', 'dist', 'locale-dictionary.js'), 'utf8');
  vm.runInNewContext(source.replace('export const APiLocaleDictionary', 'const APiLocaleDictionary') + '\nwindow.APiLocaleDictionary = APiLocaleDictionary;', context);
  const dictionary = context.window.APiLocaleDictionary;

  assert.deepEqual(Object.keys(dictionary).sort(), ['en-US', 'fr-FR', 'ja-JP', 'zh-CN']);
  assert.equal(dictionary['en-US']['因子挖掘'], 'Factor mining');
  assert.equal(dictionary['fr-FR']['纸面交易'], 'Trading simulé');
  assert.equal(dictionary['ja-JP']['因子研究'], 'ファクター研究');
});
