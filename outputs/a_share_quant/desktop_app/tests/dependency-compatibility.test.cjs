const assert = require('node:assert/strict');
const { spawnSync } = require('node:child_process');
const test = require('node:test');

test('security overrides retain the build downloader proxy API', () => {
  // Bootstrap in a child so patched global agents cannot affect other tests.
  const result = spawnSync(process.execPath, ['-e', `
    const assert = require('node:assert/strict');
    const proxy = require('global-agent');
    assert.equal(typeof proxy.bootstrap, 'function');
    assert.equal(typeof proxy.createGlobalProxyAgent, 'function');
    // The downloader calls this API (not the removed bootstrap subpath).
    proxy.bootstrap();
    assert.ok(global.GLOBAL_AGENT);
    const downloader = require('node:path').join(process.cwd(),
      'node_modules/app-builder-lib/node_modules/@electron/get/dist/cjs/proxy.js');
    require(downloader).initializeProxy();
    const CachePolicy = require('http-cache-semantics');
    assert.equal(typeof CachePolicy, 'function');
  `], { cwd: require('node:path').join(__dirname, '..'), encoding: 'utf8', timeout: 10000,
    env: { ...process.env, GLOBAL_AGENT_HTTP_PROXY: '', GLOBAL_AGENT_HTTPS_PROXY: '' } });
  assert.equal(result.status, 0, result.stderr);
});
