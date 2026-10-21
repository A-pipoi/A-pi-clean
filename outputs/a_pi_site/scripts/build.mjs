import { mkdir, readdir, readFile, writeFile, rename, copyFile, open, unlink, mkdtemp, rm } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { gzipSync } from 'node:zlib';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const sourceRoot = path.join(projectRoot, 'dist');
const outputRoot = path.join(projectRoot, 'build');

await mkdir(outputRoot, { recursive: true });
const lockPath = path.join(outputRoot, '.build.lock');
const lock = await open(lockPath, 'wx');
try {
  const names = (await readdir(sourceRoot)).sort();
  const hash = createHash('sha256');
  for (const name of names) {
    hash.update(name + '\0'); hash.update(await readFile(path.join(sourceRoot, name)));
  }
  hash.update(await readFile(fileURLToPath(import.meta.url)));
  hash.update(await readFile(path.join(projectRoot, 'package-lock.json')));
  const release = hash.digest('hex').slice(0, 20);
  const releases = path.join(outputRoot, 'releases');
  await mkdir(releases, { recursive: true });
  const stage = await mkdtemp(path.join(outputRoot, '.staging-'));
  const assets = path.join(stage, 'assets');
  await mkdir(assets);
  const js = await build({
    plugins: [{name:'zrender-trusted-types-empty-clears',setup(builder) {
      // This narrowly replaces TWO audited empty clears only. No HTML policy,
      // sanitizer bypass, global monkey patch or arbitrary vendor rewrite.
      builder.onLoad({filter:/[\\/]zrender[\\/]lib[\\/]canvas[\\/]Painter\.js$/},async args=>{
        const source=await readFile(args.path,'utf8');
        const pattern=/(this\.root|root)\.innerHTML = '';/g;
        if([...source.matchAll(pattern)].length!==2) throw new Error('Pinned zrender empty-clear audit changed; review before release');
        return {contents:source.replace(pattern,'$1.replaceChildren();'),loader:'js',resolveDir:path.dirname(args.path)};
      });
    }}],
    entryPoints: { app: path.join(sourceRoot, 'app.js'), startup: path.join(sourceRoot, 'startup.js') },
    outdir: assets, bundle: true, minify: true, format: 'esm', splitting: true, chunkNames: 'chunk-[hash]',
    entryNames: '[name]-[hash]', target: ['es2022'], sourcemap: false,
    metafile: true, logLevel: 'warning',
  });
  await build({
    stdin: { contents: ['design-tokens.css','styles.css','account-security.css','typography.css','theme.css','workbench-layers.css','reference-theme.css','workbench-usability.css'].map(name => `@import "./${name}";`).join('\n'), resolveDir: sourceRoot, loader: 'css' },
    outfile: path.join(assets, 'workbench.css'), bundle: true, minify: true,
    target: ['es2022'], sourcemap: false, logLevel: 'warning',
    loader: { '.png': 'file', '.svg': 'file', '.woff2': 'file' },
  });
  const cssBytes = await readFile(path.join(assets, 'workbench.css'));
  const cssName = `workbench-${createHash('sha256').update(cssBytes).digest('hex').slice(0, 16)}.css`;
  await rename(path.join(assets, 'workbench.css'), path.join(assets, cssName));
  for (const name of names.filter(name => !/\.(js|mjs|css|html|map)$/.test(name)))
    await copyFile(path.join(sourceRoot, name), path.join(assets, name));
  for (const name of ['LICENSE','NOTICE'])
    await copyFile(path.join(projectRoot,'node_modules','echarts',name),path.join(assets,`echarts-${name}.txt`));
  await copyFile(path.join(projectRoot,'node_modules','zrender','LICENSE'),path.join(assets,'zrender-LICENSE.txt'));
  const entries = Object.entries(js.metafile.outputs);
  const entry = name => path.basename(entries.find(([, value]) => value.entryPoint?.replaceAll('\\','/').endsWith(`/${name}.js`))[0]);
  const prefix = `/releases/${release}/assets/`;
  const helpPage = (await readFile(path.join(sourceRoot, 'help.html'), 'utf8'))
    .replaceAll('__WORKBENCH_CSS__', prefix + cssName)
    .replaceAll('__STARTUP_JS__', prefix + entry('startup'));
  await writeFile(path.join(assets, 'help.html'), helpPage);
  const sri = async name => 'sha384-' + createHash('sha384').update(await readFile(path.join(assets, name))).digest('base64');
  let html = await readFile(path.join(sourceRoot, 'index.html'), 'utf8');
  html = html.replace(/<script src="startup\.js"><\/script>/, `<script src="${prefix}${entry('startup')}" integrity="${await sri(entry('startup'))}" crossorigin="anonymous"></script>`);
  html = html.replace(/\s*<link rel="stylesheet" href="(?:design-tokens|styles|account-security|typography|theme|workbench-layers|reference-theme|workbench-usability)\.css[^\"]*"\s*\/>/g, '');
  html = html.replace('</head>', `<meta name="a-pi-release" content="${release}" /><link rel="stylesheet" href="${prefix}${cssName}" integrity="${await sri(cssName)}" crossorigin="anonymous" /></head>`);
  html = html.replace('href="help.html"', `href="${prefix}help.html"`);
  html = html.replace(/<script type="module" src="app\.js[^\"]*"><\/script>/, `<script type="module" src="${prefix}${entry('app')}" integrity="${await sri(entry('app'))}" crossorigin="anonymous"></script>`);
  for (const name of names.filter(name => /\.(png|svg|webp|ico)$/.test(name))) {
    html = html.replaceAll(`src="${name}"`, `src="${prefix}${name}"`)
      .replaceAll(`href="${name}"`, `href="${prefix}${name}"`);
  }
  if (/\?v=|src="app\.js|href="styles\.css/.test(html)) throw new Error('Unresolved source asset reference');
  const files = {};
  for (const name of (await readdir(assets)).sort()) {
    const data = await readFile(path.join(assets, name));
    files[prefix + name] = { bytes: data.length, sha256: createHash('sha256').update(data).digest('hex') };
  }
  const manifest = { release, entries: { app: prefix + entry('app'), startup: prefix + entry('startup'), css: prefix + cssName }, files };
  for (const name of await readdir(assets))
    if (/\.(js|css)$/.test(name)) await writeFile(path.join(assets, name + '.gz'), gzipSync(await readFile(path.join(assets, name)), { level: 9 }));
  await writeFile(path.join(stage, 'manifest.json'), JSON.stringify(manifest, null, 2));
  await writeFile(path.join(stage, 'index.html'), html);
  const target = path.join(releases, release);
  try { await rename(stage, target); }
  catch (error) {
    const previous = JSON.parse(await readFile(path.join(target, 'manifest.json'), 'utf8'));
    if (JSON.stringify(previous) !== JSON.stringify(manifest)) throw error;
    if (path.dirname(stage) !== outputRoot || !path.basename(stage).startsWith('.staging-')) throw new Error('Unsafe staging cleanup path');
    await rm(stage, { recursive: true, force: true });
  }
  // Keep the old entry alongside its immutable assets for a hash-checked rollback.
  try {
    const oldHtml = await readFile(path.join(outputRoot, 'index.html'), 'utf8');
    const oldRelease = /name="a-pi-release" content="([0-9a-f]{20})"/.exec(oldHtml)?.[1];
    if (oldRelease) await writeFile(path.join(releases, oldRelease, 'index.html'), oldHtml);
  } catch (error) { if (error.code !== 'ENOENT') throw error; }
  await writeFile(path.join(outputRoot, '.index.next'), html);
  await writeFile(path.join(outputRoot, '.release.next'), JSON.stringify(manifest, null, 2));
  await rename(path.join(outputRoot, '.release.next'), path.join(outputRoot, 'release.json'));
  await rename(path.join(outputRoot, '.index.next'), path.join(outputRoot, 'index.html'));
  for (const name of await readdir(outputRoot))
    if (/\.(js|mjs|css|png|map)$/.test(name)) await unlink(path.join(outputRoot, name));
  console.log(`Published ${release}: two entry bundles + lazy/shared chunks + one CSS bundle; ${Object.values(files).reduce((sum, file) => sum + file.bytes, 0)} asset bytes.`);
} finally {
  await lock.close(); await unlink(lockPath);
}
