import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname,join} from 'node:path';
import {JSDOM} from 'jsdom';

const root=join(dirname(fileURLToPath(import.meta.url)),'..');
const read=path=>readFileSync(join(root,path),'utf8');

test('full manual covers the six current workbench pages and keeps its section links valid',()=>{
 const html=read('dist/help.html'),dom=new JSDOM(html),doc=dom.window.document;
 for(const label of ['组合总览','因子研究','因子挖掘','回测实验','风险控制','纸面交易']) assert.ok(doc.body.textContent.includes(label));
 for(const link of doc.querySelectorAll('a[href^="#"]')) assert.ok(doc.getElementById(link.hash.slice(1)),`missing section ${link.hash}`);
 assert.equal(doc.querySelectorAll('main.help-manual section').length,10);
 assert.match(doc.body.textContent,/Tushare/);
 assert.match(doc.body.textContent,/不会发送给券商订单|不会发送给券商/);
 dom.window.close();
});

test('help entry is centered in the live-status dialog and full manual uses responsive shared theme styles',()=>{
 const app=read('dist/app-shell.mjs'),css=read('dist/reference-theme.css');
 assert.match(app,/class="manual-guide-link"/);
 assert.match(css,/\.utility-dialog-body \.manual-guide-link\s*\{[^}]*text-align:\s*center/s);
 assert.match(css,/\.help-manual\s*\{[^}]*box-sizing:\s*border-box/s);
});
