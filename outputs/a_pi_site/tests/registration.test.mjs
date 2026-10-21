import test from 'node:test';
import assert from 'node:assert/strict';
import {createAuthClient} from '../dist/auth-client.mjs';
import {registrationDictionary,registrationText} from '../dist/registration-view.mjs';

test('registration is anonymous JSON + CSRF and never creates a client session',async()=>{
  const calls=[];
  const client=createAuthClient({origin:'http://localhost:8000',onExpired(){assert.fail();},fetch:async(path,init)=>{
    calls.push([path,init]);
    return new Response(JSON.stringify({ok:true,username:'researcher'}),{status:201});
  }});
  assert.equal(await client.register('researcher','test-long-password','0123456789abcdef'),'researcher');
  assert.equal(client.current(),null);
  assert.equal(calls.length,1);
  assert.equal(calls[0][0],'/api/auth/register');
  assert.equal(calls[0][1].headers.get('Content-Type'),'application/json');
  assert.equal(calls[0][1].headers.get('X-A-Pi-Request'),'1');
  assert.deepEqual(JSON.parse(calls[0][1].body),{username:'researcher',password:'test-long-password',invite_code:'0123456789abcdef'});
});

test('registration returns specific errors without signing in or storing credentials',async()=>{
  for(const [status,code] of [[403,'invite_invalid'],[409,'user_exists'],[429,'too_many_attempts'],[422,'invalid_request']]) {
    const client=createAuthClient({origin:'http://localhost:8000',onExpired(){assert.fail();},fetch:async()=>new Response(JSON.stringify({error:code}),{status})});
    await assert.rejects(client.register('name','password','code'),new RegExp(code));
    assert.equal(client.current(),null);
  }
});

test('all registration labels and errors are complete in four languages',()=>{
  const expected=Object.keys(registrationDictionary['zh-CN']).sort();
  for(const locale of ['zh-CN','en-US','fr-FR','ja-JP']) {
    assert.deepEqual(Object.keys(registrationDictionary[locale]).sort(),expected);
    for(const key of expected)assert.ok(registrationText(locale,key).length>0);
  }
  for(const key of ['user_exists','invite_invalid','too_many_attempts','invalid_request']) {
    assert.notEqual(registrationText('zh-CN',key),registrationText('en-US',key));
    assert.notEqual(registrationText('fr-FR',key),registrationText('en-US',key));
    assert.notEqual(registrationText('ja-JP',key),registrationText('en-US',key));
  }
});
