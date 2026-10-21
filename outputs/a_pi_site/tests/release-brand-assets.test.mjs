import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';

test('built release resolves favicon and brand images to audited immutable assets', () => {
  const html = readFileSync(new URL('../build/index.html', import.meta.url), 'utf8');
  const manifest = JSON.parse(readFileSync(new URL('../build/release.json', import.meta.url), 'utf8'));
  const logo = `/releases/${manifest.release}/assets/a-pi-mark.svg`;
  assert.ok(manifest.files[logo]);
  assert.ok(html.includes(`type="image/svg+xml" href="${logo}"`));
  assert.equal((html.match(new RegExp(`src="${logo}"`, 'g')) || []).length, 3);
  assert.doesNotMatch(html, /kuromi|库洛米|a-pi-256\.png/i);
  assert.ok(existsSync(new URL('../dist/a-pi-mark.svg', import.meta.url)));
  assert.ok(!existsSync(new URL('../dist/a-pi-kuromi-rounded.png', import.meta.url)));
});
