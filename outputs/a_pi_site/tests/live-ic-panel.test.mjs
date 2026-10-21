import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { configureApiClient } from '../dist/api-client.mjs';
import { createLiveICPanel } from '../dist/live-ic-panel.mjs';

test('prospective monitor renders real API state, horizons and untrusted names without HTML', async () => {
  const dom=new JSDOM('<html lang="zh-CN"><body><section id="liveICPanel"></section></body></html>');
  globalThis.document=dom.window.document;
  globalThis.MutationObserver=dom.window.MutationObserver;
  const calls=[];
  const client=configureApiClient({origin:'http://localhost:8000',fetch:async url=>{
    calls.push(url);
    const body=url==='/api/auth/session'?{authenticated:true,username:'alice',role:'user',expires_at:'2030-01-01'}:
      url==='/api/factors/live-ic'?{ok:true,monitors:[1,5,10,20].map(horizon=>({name:'<img src=x onerror=alert(1)>',version:'v1',horizon,latest_signal:'2026-10-08',latest_factor_coverage:1,signal_status:'waiting_for_mature_labels',ic_series:horizon===1?[{date:'2026-10-08',value:-.12}]:[],decay:{status:'insufficient_data'}}))}:
      {ok:true,coverage:.99982,rows:{S:{available_at:'2026-10-07T23:59:10+08:00'}}};
    return new Response(JSON.stringify(body));
  }});
  await client.boot();
  const panel=createLiveICPanel();
  await panel.refresh();
  assert.match(document.querySelector('#liveICPanel').textContent,/99\.982%/);
  assert.match(document.querySelector('tbody').textContent,/等待标签成熟/);
  assert.equal(document.querySelector('img'),null);
  const select=document.querySelector('select');select.value='1';select.dispatchEvent(new dom.window.Event('change'));
  assert.equal(select.parentElement.className,'live-ic-actions');
  assert.equal(select.nextElementSibling.textContent,'刷新监控状态');
  assert.match(document.querySelector('tbody').textContent,/-0\.1200/);
  assert.deepEqual(calls.filter(url=>!url.startsWith('/api/auth/')),['/api/factors/live-ic','/api/factors/industry-pit']);
  document.documentElement.lang='en-US';await new Promise(resolve=>setTimeout(resolve,0));
  assert.match(document.querySelector('h2').textContent,/Prospective/);
  dom.window.close();
  delete globalThis.document;delete globalThis.MutationObserver;
});
