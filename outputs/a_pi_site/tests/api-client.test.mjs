import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { apiFetch, configureApiClient, getAuthClient } from '../dist/api-client.mjs';

test('module requests fail closed before boot, then share session and CSRF without patching global fetch', async () => {
  const original = globalThis.fetch;
  const calls = [];
  const session = { authenticated: true, username: 'alice', role: 'user', expires_at: '2030-01-01' };
  let expired = 0;
  const client = configureApiClient({ origin: 'http://localhost:8000', onExpired: () => expired++, fetch: async (url, options) => {
    calls.push({ url, options });
    return new Response(JSON.stringify(session), { status: url === '/api/expired' ? 401 : 200 });
  } });
  assert.equal((await apiFetch('/api/paper/cycle', { method: 'POST' })).status, 401);
  assert.equal(calls.length, 0);
  assert.equal(getAuthClient(), client);
  await client.boot();
  await apiFetch('/api/paper/cycle', { method: 'POST' });
  assert.equal(calls.at(-1).options.headers.get('X-A-Pi-Request'), '1');
  await apiFetch('/api/expired');
  assert.equal(expired, 1);
  assert.equal((await apiFetch('/api/config')).status, 401);
  assert.equal(globalThis.fetch, original);
});

test('all business modules explicitly import API transport and never overwrite native fetch', () => {
  const root = new URL('../dist/', import.meta.url);
  for (const name of readdirSync(root).filter(name => /\.(js|mjs)$/.test(name))) {
    const code = readFileSync(new URL(name, root), 'utf8');
    assert.doesNotMatch(code, /(?:window|globalThis)\.fetch\s*=/);
    if (/(?<![.\w])fetch\s*\(/.test(code)) assert.match(code, /import.*apiFetch as fetch/);
  }
});
