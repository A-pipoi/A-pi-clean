// Mechanical imports; never rewrite injected client.fetch/options.fetch.
import { readdir, readFile, writeFile } from 'node:fs/promises';
const root = new URL('../dist/', import.meta.url);
for (const name of await readdir(root)) {
  if (!/\.(mjs|js)$/.test(name) || name === 'api-client.mjs') continue;
  const file = new URL(name, root);
  const text = await readFile(file, 'utf8');
  if (/(?<![.\w])fetch\s*\(/.test(text) && !text.includes('apiFetch as fetch')) {
    await writeFile(file, 'import { apiFetch as fetch } from "./api-client.mjs";\n' + text);
    console.log(name);
  }
}
