/** Offline, production-source visual QA. Does not authenticate or contact APIs.
 * Fixture content is the real index markup, including explicit unavailable data.
 * It is NOT evidence of live desktop/business/data readiness.
 */
import { readFile, mkdir, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { build } from 'esbuild';
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const runtime = process.env.A_PI_PLAYWRIGHT_ROOT;
if (!runtime) throw new Error('Set A_PI_PLAYWRIGHT_ROOT to the bundled playwright package directory');
const { chromium } = await import(pathToFileURL(path.join(runtime, 'index.mjs')).href);
const browser = await chromium.launch({channel:'msedge',headless:true});
const out = path.join(root, 'docs', 'visual-reference-20261009');
await mkdir(out,{recursive:true});
const styles = (await Promise.all(['design-tokens','styles','account-security','typography','theme','workbench-layers','reference-theme'].map(n=>readFile(path.join(root,'dist',n+'.css'),'utf8')))).join('\n');
const logo = await readFile(path.join(root,'dist','a-pi-mark.svg'));
const logoURL = 'data:image/svg+xml;base64,'+logo.toString('base64');
let html = (await readFile(path.join(root,'dist','index.html'),'utf8'))
  .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi,'')
  .replace(/<link\b[^>]*>/gi,'')
  .replaceAll('src="a-pi-mark.svg"','src="'+logoURL+'"');
html = html.replace('</head>','<style>'+styles+'</style></head>');
const results = [];
const noticeChecks = [];
const pages = ['overview','research','factorMining','backtest','risk','paper'];
const page = await browser.newPage();
// Run only the real theme module, never the authenticated application bootstrap.
const themeBundle = await build({entryPoints:[path.join(root,'dist/theme.mjs')],bundle:true,write:false,format:'iife',globalName:'APiThemeFixture'});
await page.route('**/*',route=>route.abort());
function luminance(hex){const rgb=hex.match(/\d+/g).slice(0,3).map(Number).map(v=>{v/=255;return v<=.04045?v/12.92:((v+.055)/1.055)**2.4;});return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;}
try {
  for (const theme of ['light','dark']) {
    for (const width of [1800,1024,390]) {
      await page.setViewportSize({width,height:1100});
      await page.setContent(html);
      await page.addScriptTag({content:themeBundle.outputFiles[0].text});
      await page.evaluate(()=>window.APiThemeFixture.installTheme());
      await page.evaluate(theme=>{
        document.documentElement.dataset.theme=theme;
        document.getElementById('authView').classList.add('hidden');
        document.getElementById('appShell').classList.add('authenticated');
        document.querySelectorAll('img').forEach(img=>img.loading='eager');
      },theme);
      await page.locator('.topbar .theme-control select').selectOption(theme);
      for (const section of pages) {
        await page.evaluate(section=>{
          document.querySelectorAll('.page-section').forEach(el=>el.classList.toggle('active',el.id===section));
          document.querySelectorAll('.nav-item').forEach(el=>el.classList.toggle('active',el.dataset.section===section));
          const selected=document.querySelector('.nav-item.active');
          const rail=selected.parentElement;
          if(rail.scrollWidth>rail.clientWidth)rail.scrollTo({left:selected.offsetLeft-rail.offsetLeft-(rail.clientWidth-selected.offsetWidth)/2,behavior:'auto'});
          window.scrollTo(0,0);
        },section);
        const metrics=await page.evaluate(()=>{
          const s=getComputedStyle(document.body);
          const panel=document.querySelector('.page-section.active .panel')||document.querySelector('.metric-card');
          const active=document.querySelector('.nav-item.active');
          return {
            pageWidth:document.documentElement.scrollWidth,
            viewport:innerWidth,
            bg:s.backgroundColor,
            text:s.color,
            panelBg:getComputedStyle(panel).backgroundColor,
            panelText:getComputedStyle(panel).color,
            mainGradient:getComputedStyle(document.querySelector('.primary-metric')).backgroundImage,
            navBg:getComputedStyle(active).backgroundColor,
            navText:getComputedStyle(active).color,
            mainBg:getComputedStyle(document.querySelector('.main-content')).backgroundColor,
            brand:[...document.querySelectorAll('img[src^="data:image/svg+xml"]')].map(img=>({filter:getComputedStyle(img).filter,opacity:getComputedStyle(img).opacity,fit:getComputedStyle(img).objectFit})),
          };
        });
        if(section === 'risk' || section === 'research') {
          // Exercise both real alert classes using the existing unavailable-data copy.
          // This verifies material/contrast only and does not invent a financial warning.
          for(const variant of section === 'risk' ? ['info-alert','warning-alert'] : ['pipeline']) {
            const notice=await page.evaluate(variant=>{
              const el=variant==='pipeline' ? document.getElementById('factorPipelineStatus') : document.querySelector('#riskAlerts .alert-card');
              if(variant!=='pipeline') {el.classList.remove('info-alert','warning-alert');el.classList.add(variant);}
              const style=getComputedStyle(el);
              const texts=[el,...el.querySelectorAll('strong,p,small')].map(node=>({color:getComputedStyle(node).color,text:node.textContent}));
              return {background:style.backgroundColor,texts};
            },variant);
            const background=luminance(notice.background);
            const contrasts=notice.texts.map(item=>{const text=luminance(item.color);return (Math.max(background,text)+.05)/(Math.min(background,text)+.05);});
            noticeChecks.push({theme,width,section,variant,...notice,contrasts});
            if(width===1800)await page.screenshot({path:path.join(out,`${theme}-${section}-${variant}.png`)});
          }
        }
        const a=luminance(metrics.panelBg),b=luminance(metrics.panelText);
        const contrast=(Math.max(a,b)+.05)/(Math.min(a,b)+.05);
        const na=luminance(metrics.navBg),nb=luminance(metrics.navText);
        const navContrast=(Math.max(na,nb)+.05)/(Math.min(na,nb)+.05);
        results.push({theme,width,section,...metrics,contrast,navContrast});
        if(width!==1024) await page.screenshot({path:path.join(out,`${theme}-${width}-${section}.png`)});
      }
      if(width===1800){
        // Real login markup; no input, login or session restoration is performed.
        await page.evaluate(()=>{document.getElementById('appShell').classList.remove('authenticated');document.getElementById('authView').classList.remove('hidden');});
        await page.screenshot({path:path.join(out,`${theme}-login.png`)});
        await page.evaluate(()=>{document.documentElement.classList.add('desktop-startup');});
        await page.emulateMedia({reducedMotion:'reduce'});
        await page.screenshot({path:path.join(out,`${theme}-splash.png`)});
        await page.emulateMedia({reducedMotion:'no-preference'});
      }
    }
  }
  await writeFile(path.join(out,'checks.json'),JSON.stringify({scope:'offline existing markup only; no API, authentication, business actions or actual desktop verification',brandSha256:createHash('sha256').update(logo).digest('hex'),results,noticeChecks},null,2));
  const issues=results.filter(r=>r.pageWidth>r.viewport+1||r.contrast<4.5||r.navContrast<4.5||r.brand.some(b=>b.filter!=='none'||b.opacity!=='1'));
  const noticeIssues=noticeChecks.filter(r=>r.contrasts.some(value=>value<4.5));
  console.log(JSON.stringify({captures:34,checks:results.length,noticeChecks:noticeChecks.length,issues:issues.map(r=>({theme:r.theme,width:r.width,section:r.section,pageWidth:r.pageWidth,contrast:r.contrast})),noticeIssues},null,2));
  if(issues.length || noticeIssues.length)process.exitCode=1;
} finally {await browser.close();}
