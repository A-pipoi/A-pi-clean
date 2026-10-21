import test from 'node:test';
import assert from 'node:assert/strict';
import {validateInitialCash,buildBacktestPreset} from '../dist/backtest-research-controls.mjs';
import {validatePaperOrder} from '../dist/paper-order-dialog.mjs';
import {alignBenchmark} from '../dist/charts.mjs';

test('capital is configured, finite and bounded for backtest and sensitivity',()=>{
  assert.equal(validateInitialCash('250000'),250000);
  for(const cash of [0,-1,Infinity,NaN,100000001,'']) assert.throws(()=>validateInitialCash(cash));
  assert.equal(buildBacktestPreset({doc:{getElementById:id=>id==='backtestInitialCash'?{value:'250000'}:null}}).initial_cash,250000);
  assert.throws(()=>buildBacktestPreset({doc:{getElementById:id=>id==='backtestInitialCash'?{value:''}:null}}));
});
test('order side is explicit, lots validated and malformed codes rejected',()=>{
  assert.throws(()=>validatePaperOrder('000001','','100'));
  assert.throws(()=>validatePaperOrder('000001','BUY','123'));
  assert.throws(()=>validatePaperOrder('<img onerror=x>','BUY',100));
  assert.deepEqual(validatePaperOrder('000001.sz','SELL',123),{symbol:'000001.SZ',side:'SELL',quantity:123});
  assert.equal(validatePaperOrder('688001.SH','BUY',201).quantity,201);
});
test('benchmark dates are aligned without fabricating missing bars',()=>{
  const curve=[{date:'2026-01-01',equity:100},{date:'2026-01-02',equity:110}];
  assert.deepEqual(alignBenchmark(curve,[{date:'20260102',equity:102},{date:'20260101',equity:100}]),[1,1.02]);
  assert.equal(alignBenchmark(curve,[{date:'20260101',equity:100}]),null);
});
