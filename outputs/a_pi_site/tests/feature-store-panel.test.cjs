const assert = require('node:assert/strict');
const test = require('node:test');

test('feature warm-up is fixed to the registered historical CSI 300 universe', async () => {
  const { buildPITMaterializePayload } = await import('../dist/feature-store-panel.mjs');
  const payload = buildPITMaterializePayload({ startDate: '2024-01-02', endDate: '2026-09-29' });

  assert.equal(payload.universe_mode, 'pit_index');
  assert.equal(payload.universe_index_code, '000300.SH');
  assert.equal(payload.start_date, '2024-01-02');
  assert.equal(payload.end_date, '2026-09-29');
  assert.equal(payload.adjust, '');
  assert.equal(Object.hasOwn(payload, 'symbols'), false);
});

test('feature warm-up defaults to the existing backtest start and latest provider date', async () => {
  const { buildPITMaterializePayload } = await import('../dist/feature-store-panel.mjs');
  assert.deepEqual(buildPITMaterializePayload(), {
    universe_mode: 'pit_index',
    universe_index_code: '000300.SH',
    start_date: '2024-01-02',
    end_date: '',
    adjust: '',
    catch_up: true,
    resume: true,
  });
});
