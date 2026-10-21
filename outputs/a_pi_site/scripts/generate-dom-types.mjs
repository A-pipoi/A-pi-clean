import { readFile, writeFile, readdir } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceNames = (await readdir(path.join(projectRoot, 'dist'))).filter(name => /\.(html|js|mjs)$/.test(name));
const markup = (await Promise.all(sourceNames.map(name => readFile(path.join(projectRoot, 'dist', name), 'utf8')))).join('\n');
const tagTypes = new Map([
  ['a', 'HTMLAnchorElement'],
  ['button', 'HTMLButtonElement'],
  ['canvas', 'HTMLCanvasElement'],
  ['dialog', 'HTMLDialogElement'],
  ['details', 'HTMLDetailsElement'],
  ['form', 'HTMLFormElement'],
  ['input', 'HTMLInputElement'],
  ['label', 'HTMLLabelElement'],
  ['option', 'HTMLOptionElement'],
  ['select', 'HTMLSelectElement'],
  ['textarea', 'HTMLTextAreaElement'],
]);

const elements = new Map();
const tagPattern = /<([a-z][\w-]*)\b([^>]*)>/gi;
for (const match of markup.matchAll(tagPattern)) {
  const id = match[2].match(/\bid\s*=\s*["']([^"']+)["']/i)?.[1];
  if (!id || !/^[\w-]+$/.test(id)) continue;
  const type = tagTypes.get(match[1].toLowerCase()) || 'HTMLElement';
  const previous = elements.get(id);
  if (previous && previous !== type) throw new Error(`Duplicate id with conflicting tags: ${id}`);
  elements.set(id, type);
}

const entries = [...elements.entries()].sort(([left], [right]) => left.localeCompare(right));
const declaration = [
  '// Generated from static and module-created markup by scripts/generate-dom-types.mjs. Do not edit by hand.',
  'interface APiDomElementMap {',
  ...entries.map(([id, type]) => `  ${JSON.stringify(id)}: ${type};`),
  '}',
  '',
  'interface Document {',
  '  getElementById<K extends keyof APiDomElementMap>(elementId: K): APiDomElementMap[K] | null;',
  '}',
  '',
  'interface Document {',
  '  querySelectorAll<E extends HTMLElement = HTMLElement>(selectors: string): NodeListOf<E>;',
  '}',
  'interface Element {',
  '  querySelectorAll<E extends HTMLElement = HTMLElement>(selectors: string): NodeListOf<E>;',
  '}',
  '',
].join('\n');

const { format } = await import('prettier');
await writeFile(path.join(projectRoot, 'types', 'dom-elements.generated.d.ts'), await format(declaration, { parser: 'typescript' }), 'utf8');
console.log(`Generated DOM types for ${entries.length} application element IDs.`);
