const assert = require('node:assert/strict');
const test = require('node:test');

test('freshness line shows as-of date, trading-day lag and Beijing warm-up time', async () => {
  const { formatFeatureFreshness } = await import('../dist/feature-store-panel.mjs');
  assert.equal(
    formatFeatureFreshness({ max_feature_date: '2026-09-29', lag_days: 11, lag_unit: 'trading_days', last_materialized_at: '2026-10-15T01:30:00+00:00' }, 'zh-CN'),
    '特征截至 2026-09-29 · 落后 11 个交易日 · 上次预热 2026-10-15 09:30（北京时间）',
  );
});

test('freshness line degrades when fields are missing', async () => {
  const { formatFeatureFreshness } = await import('../dist/feature-store-panel.mjs');
  assert.equal(formatFeatureFreshness({ rows: 5 }, 'zh-CN'), '');
  assert.equal(formatFeatureFreshness({ max_feature_date: '2026-09-29', lag_days: 3, lag_unit: 'calendar_days' }, 'zh-CN'), '特征截至 2026-09-29 · 落后 3 天 · 上次预热 未记录');
  assert.equal(formatFeatureFreshness({ max_feature_date: '2026-09-29', lag_days: null, last_materialized_at: 'bad' }, 'en-US'), 'Features through 2026-09-29 · Last warm-up not recorded');
});

test('warm-up shard and resume fields render in all four locales', async () => {
  const { formatFeatureFreshness } = await import('../dist/feature-store-panel.mjs');
  const store = {
    max_feature_date: '2026-09-29',
    warmup: { completed_shards: 7, pending_months: ['2026-10'], resume_from: '2026-10-01' },
  };
  for (const lang of ['zh-CN', 'en-US', 'fr-FR', 'ja-JP']) {
    const result = formatFeatureFreshness(store, lang);
    assert.match(result, /7/);
    assert.match(result, /2026-10-01/);
    assert.doesNotMatch(result, /undefined|NaN/);
  }
  assert.match(formatFeatureFreshness({ ...store, warmup: { pending_months: ['2026-10'] } }, 'zh-CN'), /可增量追赶/);
  for (const warmup of [null, [], 'invalid', 1]) {
    assert.equal(formatFeatureFreshness({ max_feature_date: '2026-09-29', warmup }, 'zh-CN'),
      '特征截至 2026-09-29 · 上次预热 未记录');
  }
});
