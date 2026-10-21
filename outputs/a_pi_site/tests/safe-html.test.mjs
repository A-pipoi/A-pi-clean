import test from 'node:test';
import assert from 'node:assert/strict';
import {JSDOM} from 'jsdom';
import {readFileSync,readdirSync} from 'node:fs';
import {setSafeHtml,appendSafeHtml} from '../dist/safe-html.mjs';
test('all application HTML writes pass through the central renderer',()=>{
 for(const name of readdirSync(new URL('../dist/',import.meta.url)).filter(n=>/\.(js|mjs)$/.test(n))){
  const text=readFileSync(new URL('../dist/'+name,import.meta.url),'utf8');
  assert.doesNotMatch(text,/\.innerHTML\s*=(?!=)|\.outerHTML\s*=(?!=)|\.insertAdjacentHTML\s*\(/,name);
 }
});
test('XSS payloads lose executable markup without reparsing sanitized fragments',()=>{
 const dom=new JSDOM('<div id="target"></div>');const target=dom.window.document.querySelector('#target');
 setSafeHtml(target,'<img src="x" onerror="alert(1)"><a href="javascript:alert(1)">link</a><script>alert(1)</script><svg onload="alert(1)"><foreignObject><iframe srcdoc="x"></iframe></foreignObject><path d="M1 1"/></svg>');
 assert.equal(target.querySelectorAll('script,iframe,foreignObject,[onerror],[onload],[srcdoc]').length,0);
 assert.equal(target.querySelector('a').hasAttribute('href'),false);
 assert.equal(target.querySelectorAll('svg path').length,1);dom.window.close();
});
test('table rows, dropdown options, safe attributes and clearing survive sanitizer',()=>{
 const dom=new JSDOM('<table><tbody></tbody></table><select></select>');const doc=dom.window.document;
 const body=doc.querySelector('tbody');setSafeHtml(body,'<tr><td class="num" data-id="1">2.00</td></tr>');
 assert.equal(body.rows.length,1);assert.equal(body.rows[0].cells[0].textContent,'2.00');
 const select=doc.querySelector('select');appendSafeHtml(select,'<option value="baseline">Baseline</option>');
 assert.equal(select.options.length,1);setSafeHtml(body,'');assert.equal(body.rows.length,0);dom.window.close();
});
