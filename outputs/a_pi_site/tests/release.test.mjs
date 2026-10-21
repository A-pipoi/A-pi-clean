import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { createHash } from 'node:crypto';
import { gunzipSync } from 'node:zlib';

test('published HTML uses one immutable release, real bundled assets and no production sourcemaps', () => {
  const root = new URL('../build/', import.meta.url);
  const manifest = JSON.parse(readFileSync(new URL('release.json', root)));
  const html = readFileSync(new URL('index.html', root), 'utf8');
  assert.match(html, new RegExp(`a-pi-release" content="${manifest.release}`));
  assert.doesNotMatch(html, /\?v=|src="app\.js|href="styles\.css/);
  assert.equal((html.match(/<script\b/g) || []).length, 2);
  assert.equal((html.match(/rel="stylesheet"/g) || []).length, 1);
  for (const [url, file] of Object.entries(manifest.files)) {
    assert.ok(url.startsWith(`/releases/${manifest.release}/assets/`));
    const data = readFileSync(new URL('.' + url, root));
    assert.equal(createHash('sha256').update(data).digest('hex'), file.sha256);
    if (/\.(js|css)$/.test(url)) assert.deepEqual(gunzipSync(readFileSync(new URL('.' + url + '.gz', root))), data);
  }
  const assets = new URL(`releases/${manifest.release}/assets/`, root);
  assert.ok(!readdirSync(assets).some(name => name.endsWith('.map') || name.endsWith('.mjs')));
  for(const url of Object.keys(manifest.files).filter(url=>url.endsWith('.js'))) {
    const code=readFileSync(new URL('.'+url,root),'utf8');
    for(const match of code.matchAll(/["']\.\/([^"']+\.js)["']/g))
      assert.ok(manifest.files[`/releases/${manifest.release}/assets/${match[1]}`], 'every lazy/shared import belongs to this immutable release');
  }
});
