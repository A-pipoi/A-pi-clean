const fs = require('node:fs');
const path = require('node:path');
const asar = require('@electron/asar');
const { verifyRuntimeParity } = require('./modules/bundle-parity.cjs');

const outputDir = path.resolve(process.argv[2] || path.join(__dirname, 'dist', 'win-unpacked'));
const required = [
  'A-pi 量化工作台.exe',
  'icudtl.dat',
  'v8_context_snapshot.bin',
  path.join('locales', 'zh-CN.pak'),
  path.join('resources', 'app.asar'),
];
const missing = required.filter((name) => {
  const fullPath = path.join(outputDir, name);
  return !fs.existsSync(fullPath) || fs.statSync(fullPath).size === 0;
});
const asarPath = path.join(outputDir, 'resources', 'app.asar');
if (fs.existsSync(asarPath)) {
  for (const name of ['main.cjs', 'modules/service.cjs', 'modules/windows.cjs']) {
    try {
      asar.statFile(asarPath, name);
    } catch {
      missing.push(`app.asar:${name}`);
    }
  }
  for (const name of verifyRuntimeParity(asar, asarPath, __dirname)) {
    missing.push(`app.asar:stale-or-missing:${name}`);
  }
}

if (missing.length) {
  console.error(`A-pi 桌面包不完整：${missing.join(', ')}`);
  process.exitCode = 1;
} else {
  console.log(`A-pi 桌面包运行文件完整：${outputDir}`);
}
