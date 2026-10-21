// Chromium visual/interaction QA with a static fixture, never a production session.
const { app, BrowserWindow } = require('electron');
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const assert = require('node:assert/strict');
const esbuild = require('esbuild');
const root = path.resolve(__dirname, '..');
const out = path.resolve(root, '../auth_release/sidebar-dock-qa-20261013');
fs.mkdirSync(out, {recursive: true});
// Windows GUI executables can outlive the invoking tool's stdout/stderr pipes.
// Persist diagnostics instead of console writes, which can raise EPIPE popups.
for (const stream of [process.stdout, process.stderr]) stream?.on('error', (error) => {
  if (error.code !== 'EPIPE') fs.appendFileSync(path.join(out, 'runner.log'), String(error)+'\n');
});
app.setPath('userData', path.join(out, 'chromium-profile'));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));
function contrastRatio(foreground, background) {
  const luminance = (color) => color.match(/[\d.]+/g).slice(0,3).map(Number).map((v) => {v /= 255;return v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4;}).reduce((sum, v, i) => sum + v * [.2126,.7152,.0722][i], 0);
  const a = luminance(foreground), b = luminance(background);
  return (Math.max(a,b)+.05)/(Math.min(a,b)+.05);
}
app.disableHardwareAcceleration();
app.whenReady().then(async () => {
  fs.mkdirSync(out, {recursive: true});
  const entry = esbuild.buildSync({entryPoints: [path.join(root, 'tests/fixtures/usability-entry.mjs')], bundle: true, format: 'iife', write: false, platform: 'browser'}).outputFiles[0].text;
  const manifest = JSON.parse(fs.readFileSync(path.join(root, 'build/release.json')));
  let html = fs.readFileSync(path.join(root, 'dist/index.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/g, '').replace(/<link rel="stylesheet"[^>]*>/g, '');
  html = html.replace('</head>', `<link rel="stylesheet" href="${manifest.entries.css}"></head>`).replace('</body>', '<script src="/fixture.js"></script></body>');
  const server = http.createServer((req, res) => {
    if (req.url === '/fixture.js') {res.setHeader('Content-Type', 'text/javascript'); res.end(entry); return;}
    if (req.url === '/' || req.url?.startsWith('/#')) {res.setHeader('Content-Type', 'text/html'); res.end(html); return;}
    const url = new URL(req.url, 'http://localhost');
    const base = url.pathname.startsWith('/releases/') ? path.join(root, 'build') : path.join(root, 'dist');
    const file = path.resolve(base, '.' + url.pathname);
    if (!file.startsWith(base + path.sep) || !fs.existsSync(file)) {res.writeHead(404); res.end(); return;}
    res.setHeader('Content-Type', file.endsWith('.css') ? 'text/css' : file.endsWith('.png') ? 'image/png' : 'application/octet-stream');
    res.end(fs.readFileSync(file));
  });
  await new Promise((r) => server.listen(0, '127.0.0.1', r));
  const win = new BrowserWindow({width: 1440, height: 1000, show: false, webPreferences: {offscreen: true, contextIsolation: true, nodeIntegration: false, backgroundThrottling: false, partition: 'usability-fixture-'+Date.now()}});
  const errors = [], report = [];
  win.webContents.on('console-message', (_event, level, message) => {if (level === 3 && !/404|favicon/.test(message)) errors.push(message);});
  try {
    await win.loadURL(`http://127.0.0.1:${server.address().port}/`);
    await wait(700);
    win.webContents.debugger.attach('1.3');
    // Hidden/offscreen windows otherwise suppress focusin events, unlike a
    // foreground workbench. Emulate foreground focus for keyboard assertions.
    await win.webContents.debugger.sendCommand('Emulation.setFocusEmulationEnabled',{enabled:true});
    for (const theme of ['light', 'dark']) for (const width of [1440, 390]) {
      await win.webContents.executeJavaScript('localStorage.clear()');
      await win.reload();
      await wait(400);
      win.setSize(width, width === 390 ? 844 : 1000);
      await win.webContents.executeJavaScript(`document.documentElement.dataset.theme='${theme}';window.__usabilityFixture.showSection('overview');window.scrollTo(0,0)`);
      await wait(150);
      const before = await win.webContents.executeJavaScript(`JSON.stringify(document.querySelector('.sidebar').getBoundingClientRect().toJSON())`);
      await win.webContents.executeJavaScript('window.scrollTo(0,600)');
      await wait(60);
      const after = await win.webContents.executeJavaScript(`JSON.stringify(document.querySelector('.sidebar').getBoundingClientRect().toJSON())`);
      if (width > 720) assert.equal(JSON.parse(before).top, JSON.parse(after).top, 'Sidebar scrolls');
      await win.webContents.executeJavaScript('window.scrollTo(0,0)');
      if (width > 720) {
        const mainBefore = await win.webContents.executeJavaScript('JSON.stringify(document.querySelector(".main-content").getBoundingClientRect().toJSON())');
        const handleRect = await win.webContents.executeJavaScript('document.querySelector(".sidebar-handle").getBoundingClientRect().toJSON()');
        assert.ok(handleRect.left >= -1 && handleRect.right <= 25, 'Handle is not exposed at the edge');
        assert.equal(await win.webContents.executeJavaScript('document.querySelector(".sidebar").inert'), true);
        win.webContents.sendInputEvent({type:'mouseMove',x:12,y:Math.round(handleRect.top+36)});
        await wait(270);
        assert.equal(await win.webContents.executeJavaScript('document.querySelector(".sidebar-dock").dataset.open'), 'true', 'Real hover does not open sidebar');
        assert.equal(await win.webContents.executeJavaScript('Boolean(document.elementFromPoint(40,50)?.closest(".sidebar"))'), true, 'Topbar obscures sidebar branding');
        assert.equal(await win.webContents.executeJavaScript('JSON.stringify(document.querySelector(".main-content").getBoundingClientRect().toJSON())'), mainBefore, 'Sidebar pushes content');
        const openShot = await win.webContents.debugger.sendCommand('Page.captureScreenshot',{format:'png'});
        fs.writeFileSync(path.join(out,`${theme}-${width}-sidebar-open.png`),Buffer.from(openShot.data,'base64'));
        win.webContents.sendInputEvent({type:'mouseMove',x:400,y:260});
        await wait(490);
        assert.equal(await win.webContents.executeJavaScript('document.querySelector(".sidebar-dock").dataset.open'), 'false', 'Sidebar does not retract');
        await win.webContents.executeJavaScript('document.querySelector(".sidebar-handle").focus()');
        assert.equal(await win.webContents.executeJavaScript('document.querySelector(".sidebar-dock").dataset.open'), 'true');
        win.webContents.sendInputEvent({type:'keyDown',keyCode:'Escape'});
        win.webContents.sendInputEvent({type:'keyUp',keyCode:'Escape'});
        await wait(30);
        assert.equal(await win.webContents.executeJavaScript('document.querySelector(".sidebar-dock").dataset.open'), 'false');
        await win.webContents.executeJavaScript('document.querySelector(".sidebar-handle").blur()');
        await win.webContents.debugger.sendCommand('Emulation.setEmulatedMedia',{features:[{name:'prefers-reduced-motion',value:'reduce'}]});
        const duration = await win.webContents.executeJavaScript('getComputedStyle(document.querySelector(".sidebar-dock")).transitionDuration');
        assert.equal(duration,'0s');
        await win.webContents.debugger.sendCommand('Emulation.setEmulatedMedia',{features:[]});
      } else {
        assert.equal(await win.webContents.executeJavaScript('document.querySelector(".sidebar").inert'), false);
        assert.equal(await win.webContents.executeJavaScript('getComputedStyle(document.querySelector(".sidebar-handle")).display'),'none');
      }
      const geometry = await win.webContents.executeJavaScript(`(() => {const ring=document.getElementById('allocationDonut'); const rect=()=>JSON.stringify(ring.getBoundingClientRect().toJSON());const before=rect();for(let i=0;i<30;i++){window.__usabilityFixture.ring.trigger('mouseover',{dataIndex:i%2});window.__usabilityFixture.ring.trigger('globalout',{});}return {before,after:rect(),overflow:document.documentElement.scrollWidth>innerWidth};})()`);
      assert.equal(geometry.before, geometry.after, 'Hover changes ring geometry');
      assert.equal(geometry.overflow, false, 'Page overflows horizontally');
      if (width > 720) {
        const rect = JSON.parse(geometry.before);
        for (let i=0;i<24;i++) {
          const angle=i*Math.PI/12;
          win.webContents.sendInputEvent({type:'mouseMove',x:Math.round(rect.x+rect.width/2+rect.width*.39*Math.cos(angle)),y:Math.round(rect.y+rect.height/2+rect.height*.39*Math.sin(angle))});
          await wait(12);
          const current = await win.webContents.executeJavaScript('JSON.stringify(document.getElementById("allocationDonut").getBoundingClientRect().toJSON())');
          assert.equal(current, geometry.before, 'Real mouse hover moved the ring');
        }
      }
      await win.webContents.executeJavaScript(`document.getElementById('notificationButton').click();document.querySelector('.notification-arrow').click()`);
      assert.equal(await win.webContents.executeJavaScript(`document.getElementById('notificationCenter').hidden`), false);
      await win.webContents.executeJavaScript(`document.querySelector('[data-notification-back]').click();document.querySelector('[data-notification-delete]').click();document.getElementById('notificationCenter').hidden=true`);
      const contrast = await win.webContents.executeJavaScript(`(() => { const get=s=>getComputedStyle(document.querySelector(s));return {theme, text:get('.notification-copy strong').color, body:get('.notification-copy > span').color, background:get('.notification-item').backgroundColor};})()`.replace('{theme,', `{theme:'${theme}',`));
      contrast.titleRatio = contrastRatio(contrast.text, contrast.background);
      contrast.bodyRatio = contrastRatio(contrast.body, contrast.background);
      assert.ok(contrast.titleRatio >= 4.5 && contrast.bodyRatio >= 4.5, 'Notification contrast is insufficient');
      for (const section of ['overview', 'factorMining', 'backtest', 'risk']) {
        await win.webContents.executeJavaScript(`window.__usabilityFixture.showSection('${section}');window.scrollTo(0,0)`);
        await wait(100);
        const image = await win.webContents.debugger.sendCommand('Page.captureScreenshot', {format: 'png'});
        fs.writeFileSync(path.join(out, `${theme}-${width}-${section}.png`), Buffer.from(image.data, 'base64'));
      }
      report.push({theme, width, fixedSidebar: width > 720, stableRing: true, overflow: false, contrast});
    }
    fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify({fixtureOnly: true, errors, report}, null, 2));
    assert.deepEqual(errors, []);
    fs.appendFileSync(path.join(out,'runner.log'),'PASS theme/viewport sidebar, hover, notifications and page captures.\n');
    server.close(); win.destroy(); app.exit(0);
  } catch (error) {
    fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({error:String(error),stack:error.stack},null,2));
    server.close();win.destroy();app.exit(1);
  }
}).catch((error) => {
  fs.writeFileSync(path.join(out,'failure.json'),JSON.stringify({error:String(error),stack:error.stack},null,2));
  app.exit(1);
});
