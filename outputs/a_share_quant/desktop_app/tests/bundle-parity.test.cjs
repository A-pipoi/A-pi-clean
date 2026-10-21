const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const { verifyRuntimeParity } = require('../modules/bundle-parity.cjs');

test('runtime verification rejects a stale HTTP service module', () => {
  const root = path.resolve(__dirname, '..');
  const archive = { extractFile: (_, name) => fs.readFileSync(path.join(root, name)) };
  assert.deepEqual(verifyRuntimeParity(archive, 'fixture', root), []);
  const stale = {
    extractFile: (_, name) => name === 'modules/service.cjs'
      ? Buffer.from('const APP_URL = "http://127.0.0.1:8000/";')
      : archive.extractFile('', name),
  };
  assert.deepEqual(verifyRuntimeParity(stale, 'fixture', root), ['modules/service.cjs']);
});
