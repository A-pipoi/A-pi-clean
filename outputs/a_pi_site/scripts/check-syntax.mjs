import { readdir } from 'node:fs/promises';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const assetRoot = path.join(projectRoot, 'dist');
const scripts = (await readdir(assetRoot, { withFileTypes: true }))
  .filter((entry) => entry.isFile() && /\.(?:js|mjs)$/.test(entry.name))
  .map((entry) => path.join(assetRoot, entry.name));

for (const script of scripts) {
  const result = spawnSync(process.execPath, ['--check', script], { encoding: 'utf8' });
  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.stdout);
    process.exit(result.status || 1);
  }
}

console.log(`Syntax checked ${scripts.length} frontend scripts.`);
