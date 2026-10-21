const assert = require('node:assert/strict');
const test = require('node:test');

test('monitorFeatureJob polls until completed and reports progress', async () => {
  const { monitorFeatureJob } = await import('../dist/feature-store-panel.mjs');
  const seen = [];
  let n = 0;
  const fetchImpl = async () => {
    n += 1;
    if (n === 1)
      return {
        ok: true,
        status: 200,
        json: async () => ({
          ok: true,
          status: 'running',
          progress: 40,
          stage: 'bars',
          message: 'pulling',
          symbol_count: 10,
          processed_symbols: 4,
        }),
      };
    return {
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        status: 'completed',
        progress: 100,
        stage: 'completed',
        result: { materialization: { written: 7 }, feature_store: { rows: 7 } },
      }),
    };
  };
  const job = await monitorFeatureJob('FM-1', {
    fetchImpl,
    intervalMs: 1,
    onProgress: (j) => seen.push(j.status),
  });
  assert.equal(job.status, 'completed');
  assert.deepEqual(seen, ['running', 'completed']);
  assert.equal(job.result.materialization.written, 7);
});

test('monitorFeatureJob surfaces failed code via describeMaterializeError', async () => {
  const { monitorFeatureJob } = await import('../dist/feature-store-panel.mjs');
  const fetchImpl = async () => ({
    ok: true,
    status: 200,
    json: async () => ({
      ok: true,
      status: 'failed',
      code: 'quota',
      hint: '额度',
      detail: 'limit',
      error: '额度 原始错误：limit',
    }),
  });
  await assert.rejects(
    () => monitorFeatureJob('FM-2', { fetchImpl, intervalMs: 1 }),
    /quota|额度|limit/,
  );
});
