const assert = require('node:assert/strict');
const test = require('node:test');

test('materialize failure shows code hint plus original error', async () => {
  const { describeMaterializeError } = await import('../dist/feature-store-panel.mjs');
  assert.equal(
    describeMaterializeError({ ok: false, code: 'tushare_token_missing', error: 'x', detail: '未配置 TUSHARE_TOKEN' }, 503, 'zh-CN'),
    '服务端未配置 TUSHARE_TOKEN，请配置后重启服务（tushare_token_missing）。原始错误：未配置 TUSHARE_TOKEN',
  );
  assert.match(describeMaterializeError({ code: 'quota', detail: 'limit' }, 429, 'en-US'), /^Tushare quota.*\(quota\)\. Original error: limit$/);
  assert.match(describeMaterializeError({ code: 'new_code', detail: 'd' }, 500, 'ja-JP'), /Unexpected error.*new_code.*d$/);
});

test('materialize failure degrades without code or payload', async () => {
  const { describeMaterializeError } = await import('../dist/feature-store-panel.mjs');
  assert.equal(describeMaterializeError({ error: 'legacy' }, 502, 'zh-CN'), 'legacy');
  assert.equal(describeMaterializeError({}, 504, 'zh-CN'), 'HTTP 504');
  assert.equal(describeMaterializeError(null, 500, 'zh-CN'), 'HTTP 500');
});
