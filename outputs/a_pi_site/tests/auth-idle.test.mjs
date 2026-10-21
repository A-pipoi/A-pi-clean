import assert from 'node:assert/strict';
import test from 'node:test';
import {readFileSync} from 'node:fs';
import {createAuthClient} from '../dist/auth-client.mjs';

test('a forbidden sensitive action does not clear the authenticated session or replay writes', async()=>{
  let reason=null, writes=0;
  const client=createAuthClient({origin:'http://localhost',onExpired:value=>{reason=value;},fetch:async(input)=>{
    if(String(input).includes('/auth/session')) return new Response(JSON.stringify({authenticated:true,username:'alice',role:'researcher',expires_at:'2030-01-01T00:00:00Z'}));
    writes++;
    return new Response(JSON.stringify({error:'reauthentication_required'}),{status:403});
  }});
  await client.boot();
  const response=await client.fetch('/api/paper/orders',{method:'POST'});
  assert.equal((await response.json()).error,'reauthentication_required');
  assert.equal(reason,null);
  assert.equal(client.current().username,'alice');
  await client.fetch('/api/paper/orders',{method:'POST'});
  assert.equal(writes,2);
});

test('legacy reauthentication marker is cleared without blocking session recovery',()=>{
  const app=readFileSync(new URL('../dist/app.js',import.meta.url),'utf8');
  assert.match(app,/sessionStorage\.getItem\("a_pi_forced_login"\)[\s\S]*?"reauthentication_required"[\s\S]*?sessionStorage\.removeItem\("a_pi_forced_login"\)/);
  assert.doesNotMatch(app,/showAuthError\("reauthentication_required"\)/);
});

test('background requests do not renew idle sessions; trusted activity renews once', async()=>{
  const target=new EventTarget();
  const calls=[];
  const client=createAuthClient({origin:'http://localhost',activityTarget:target,onExpired:()=>{},fetch:async(input,init)=>{
    calls.push(new Headers(init?.headers));
    return new Response(JSON.stringify(String(input).includes('/auth/session')?{authenticated:true,username:'alice',role:'researcher',expires_at:'2030-01-01T00:00:00Z'}:{ok:true}),{status:200});
  }});
  await client.boot();
  await client.fetch('/api/workspace');
  assert.equal(calls.at(-1).get('X-A-Pi-Activity'),null);
  target.dispatchEvent(new Event('pointerdown'));
  await client.fetch('/api/workspace');
  assert.equal(calls.at(-1).get('X-A-Pi-Activity'),null);
  const event=new Event('pointerdown');
  Object.defineProperty(event,'isTrusted',{value:true});
  target.dispatchEvent(event);
  await client.fetch('/api/workspace');
  assert.equal(calls.at(-1).get('X-A-Pi-Activity'),'1');
  await client.fetch('/api/workspace');
  assert.equal(calls.at(-1).get('X-A-Pi-Activity'),null);
  client.forget();
  const result=await client.fetch('/api/workspace');
  assert.equal(result.status,401);
});
