import test from 'node:test';
import assert from 'node:assert/strict';
import {presentationState} from '../dist/presentation-state.mjs';
test('aggregate status never turns green on stale, unknown, building or failed workspace',()=>{
 const fresh={online:true,dataReachable:true,dataFresh:true};
 assert.equal(presentationState(fresh,{ready:true}),'is-ready');
 for(const workspace of [{},{ready:true,building:true},{ready:true,error:'failed'}])assert.equal(presentationState(fresh,workspace),'is-partial');
 assert.equal(presentationState({...fresh,dataFresh:false},{ready:true}),'is-partial');
 assert.equal(presentationState(null,{ready:true}),'is-partial');
 assert.equal(presentationState({...fresh,dataReachable:false},{ready:true}),'is-unavailable');
 assert.equal(presentationState({online:false},{ready:true}),'is-unavailable');
});
