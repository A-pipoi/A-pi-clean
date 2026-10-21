import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';
import { installWorkbenchPolish, decorateEmpty } from '../dist/workbench-polish.mjs';
test('presentation preserves all existing ids, data attributes, control values and release brand assets',()=>{
 const html=readFileSync(new URL('../dist/index.html',import.meta.url),'utf8');
 const dom=new JSDOM(html,{url:'http://localhost',pretendToBeVisual:true});
 for(const key of ['window','document','MutationObserver','HTMLElement','MouseEvent'])globalThis[key]=dom.window[key];
 const fingerprint=()=>[...document.querySelectorAll('[id],input,select,textarea,img')].map(el=>[el.id,[...el.attributes].filter(a=>a.name.startsWith('data-')).map(a=>[a.name,a.value]),'value' in el?el.value:null,el.tagName==='IMG'?el.getAttribute('src'):null]).sort((a,b)=>String(a[0]).localeCompare(String(b[0])));
 const before=fingerprint();installWorkbenchPolish();assert.deepEqual(fingerprint(),before);
 // The sectioned configuration replaces the former three generated fieldsets.
 assert.equal(document.querySelectorAll('.config-group').length,0);
 assert.deepEqual([...document.querySelectorAll('[data-config-section]')].map(el=>el.dataset.configSection),['data','strategy','universe','run']);
 assert.equal(document.querySelectorAll('.metric-icon-chip').length,4);
 assert.equal(document.querySelectorAll('.nav-item .nav-icon svg').length,6);
 assert.equal(document.querySelectorAll('.parameter-unit').length,4);
 dom.window.close();
});
test('empty action uses existing navigation and never produces a trading action',()=>{
 const dom=new JSDOM('<html lang="en"><button class="nav-item" data-section="backtest"></button><div id="empty"></div></html>');
 for(const key of ['window','document','HTMLElement','MouseEvent'])globalThis[key]=dom.window[key];
 let navigated=0;document.querySelector('button').addEventListener('click',()=>navigated++);
 decorateEmpty(document.getElementById('empty'),'No observations','backtest');document.querySelector('.empty-state button').click();
 assert.equal(navigated,1);assert.equal(document.querySelector('.empty-state button').textContent,'View backtest configuration');dom.window.close();
});
test('loading stays compact; stock-selection action focuses without issuing a request',()=>{
 const dom=new JSDOM('<html lang="zh"><select id="statementSymbolSelect"><option>股票</option></select><div class="statement-empty" id="empty"></div><div id="loading"></div></html>');
 for(const key of ['window','document','HTMLElement','MouseEvent'])globalThis[key]=dom.window[key];
 decorateEmpty(document.getElementById('loading'),'正在加载真实数据','connection');
 assert.equal(document.querySelector('#loading button'),null);
 decorateEmpty(document.getElementById('empty'),'选择股票后加载报表','connection');
 document.querySelector('#empty button').click();
 assert.equal(document.activeElement.id,'statementSymbolSelect');
 assert.equal(document.querySelector('#empty button').textContent,'选择股票');
 dom.window.close();
});
test('money never receives an interpolated or hidden overlay',async()=>{
 const dom=new JSDOM('<html lang="fr"><body><strong id="paperEquity">¥ 1 000,50</strong></body></html>');
 for(const key of ['window','document','HTMLElement','MouseEvent','MutationObserver'])globalThis[key]=dom.window[key];
 installWorkbenchPolish();document.getElementById('paperEquity').textContent='¥ 2 000,75';
 await new Promise(resolve=>setTimeout(resolve,20));
 assert.equal(document.getElementById('paperEquity').textContent,'¥ 2 000,75');
 assert.equal(document.querySelector('.count-up-overlay,.count-up-source'),null);dom.window.close();
});
test('metric-card surfaces are uniform in both themes, without stale high-specificity pastel rules',()=>{
 for(const name of ['styles.css','theme.css']){
  const css=readFileSync(new URL('../dist/'+name,import.meta.url),'utf8');
  const rules=[...css.matchAll(/(?:\.metric-card:nth-child\(\d\)|\.primary-metric)\s*\{([^}]+)\}/g)];
  for(const [,body] of rules)if(body.includes('background:'))assert.match(body,/background:\s*var\(--panel\)/);
 }
});
