// Isolated browser fixture: never connects to the production API or session.
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const esbuild = require('esbuild');
const root = path.resolve(__dirname, '..');
const out = path.resolve(root, '../auth_release/paper-catchup-qa-20261014');
fs.mkdirSync(out, { recursive: true });
for (const stream of [process.stdout, process.stderr]) stream.on('error', () => {});
app.disableHardwareAcceleration();
app.setPath('userData', path.join(out, 'profile'));
app.whenReady().then(async () => {
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'build/release.json')));
  const entry = esbuild.buildSync({ stdin: { resolveDir: root, contents: `
    import { createManualPaperCatchup } from './dist/paper-manual-catchup.mjs';
    const ui = createManualPaperCatchup({translate:s=>s, clock:()=>new Date('2026-10-14T13:00:00Z'),apply:()=>{},refresh:async()=>{}});
    ui.update({enabled:true,maintenance:{latest:{run_date:'2026-10-14',status:'missed_window',attempts:0}}});
  ` }, bundle: true, write: false, format: 'iife' }).outputFiles[0].text;
  const html = `<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="${manifest.entries.css}"></head><body><main style="padding:24px"><h1>纸面交易</h1><p>隔离界面测试，非真实账户</p><div class="paper-catchup-tools"><button class="secondary-button" id="paperManualCatchupButton" aria-describedby="paperManualCatchupHint" disabled>人工补跑</button><p id="paperManualCatchupHint"></p><p id="paperManualCatchupResult" role="status" aria-live="polite" hidden></p></div></main><script src="/fixture.js"></script></body></html>`;
  const server = http.createServer((req,res)=>{
    if (req.url === '/') {res.setHeader('Content-Type','text/html'); res.end(html);return;}
    if (req.url === '/fixture.js') {res.setHeader('Content-Type','text/javascript');res.end(entry);return;}
    const file = path.resolve(root,'build','.'+new URL(req.url,'http://localhost').pathname);
    if (!file.startsWith(path.join(root,'build')+path.sep) || !fs.existsSync(file)) {res.writeHead(404);res.end();return;}
    res.setHeader('Content-Type',file.endsWith('.css')?'text/css':'application/octet-stream');res.end(fs.readFileSync(file));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const win = new BrowserWindow({show:false,width:1440,height:900,webPreferences:{offscreen:true,nodeIntegration:false,contextIsolation:true}});
  const report=[];
  try {
    await win.loadURL(`http://127.0.0.1:${server.address().port}/`);
    win.webContents.debugger.attach('1.3');
    await win.webContents.debugger.sendCommand('Emulation.setFocusEmulationEnabled',{enabled:true});
    for (const theme of ['light','dark']) for (const width of [1440,390]) {
      win.setSize(width,900);
      await win.webContents.executeJavaScript(`document.documentElement.dataset.theme='${theme}';document.getElementById('paperManualCatchupButton').click()`);
      await new Promise(resolve=>setTimeout(resolve,150));
      const result = await win.webContents.executeJavaScript(`(()=>{const d=document.querySelector('dialog');const r=d.getBoundingClientRect();return {theme:'${theme}',width,open:d.open,focus:document.activeElement.textContent,overflow:document.documentElement.scrollWidth>innerWidth,dialogOverflow:d.scrollWidth>d.clientWidth,left:r.left,right:r.right,buttonHeight:document.getElementById('paperManualCatchupButton').getBoundingClientRect().height,color:getComputedStyle(d).color,background:getComputedStyle(d).backgroundColor}})()`);
      assert.equal(result.open,true);assert.equal(result.focus,'取消');assert.equal(result.overflow,false);assert.equal(result.dialogOverflow,false);assert.ok(result.left>=0&&result.right<=width);assert.ok(result.buttonHeight>=44);assert.notEqual(result.color,result.background);
      const shot = await win.webContents.debugger.sendCommand('Page.captureScreenshot',{format:'png'});
      fs.writeFileSync(path.join(out,`${theme}-${width}.png`),Buffer.from(shot.data,'base64'));
      await win.webContents.executeJavaScript(`document.querySelector('dialog .secondary-button').click()`);
      assert.equal(await win.webContents.executeJavaScript('Boolean(document.querySelector("dialog"))'),false);
      report.push(result);
    }
    fs.writeFileSync(path.join(out,'report.json'),JSON.stringify({ok:true,release:manifest.release,checks:report},null,2));
  } finally {win.destroy();server.close();}
  app.exit(0);
}).catch(error=>{fs.writeFileSync(path.join(out,'failure.txt'),String(error.stack));app.exit(1);});
