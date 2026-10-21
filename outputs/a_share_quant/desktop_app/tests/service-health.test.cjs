const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const https = require('node:https');
const path = require('node:path');
const test = require('node:test');
const { APP_URL, APP_URL_LOCALHOST, createServiceManager } = require('../modules/service.cjs');

test('HTTPS probe verifies local CA and anonymous authentication boundary', async (t) => {
  const original = https.get;
  t.after(() => { https.get = original; });
  const requests = [];
  const root = path.resolve(__dirname, '../..');
  const responses = [
    [200, { ok: true }],
    [401, { ok: false, error: 'unauthorized' }],
    [200, { ok: true }],
    [200, { ok: true }],
  ];
  https.get = (url, options, callback) => {
    requests.push(url.toString());
    assert.equal(url.protocol, 'https:');
    assert.equal(options.rejectUnauthorized, false);
    const request = new EventEmitter();
    request.destroy = (error) => {
      if (error) process.nextTick(() => request.emit("error", error));
    };
    const [statusCode, body] = responses.shift();
    const response = new EventEmitter();
    response.statusCode = statusCode;
    response.socket = {
      getPeerCertificate: () => ({
        fingerprint256: requests.length === 3
          ? "00:UNTRUSTED"
          : service.localCertificateFingerprint(root),
      }),
    };
    response.resume = () => {};
    response.setEncoding = () => {};
    process.nextTick(() => {
      callback(response);
      response.emit('data', JSON.stringify(body));
      response.emit('end');
    });
    return request;
  };
  const service = createServiceManager({}, root);
  const health = await service.readHealth(root);
  assert.deepEqual(health, { ok: true, authentication_required: true });
  assert.equal(service.backendIsReady(health), true);
  assert.equal(await service.readHealth(root), null);
  assert.deepEqual(requests.slice(0, 2), [APP_URL + 'api/health', APP_URL + 'api/auth/session']);
  assert.equal(new URL(APP_URL_LOCALHOST).protocol, 'https:');
  assert.equal(service.backendIsReady(null), false);
});
