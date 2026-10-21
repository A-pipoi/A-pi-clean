import test from 'node:test';
import assert from 'node:assert/strict';
import {normalizedChartData} from '../dist/interactive-chart.mjs';
import {readFileSync} from 'node:fs';
test('interactive chart dates are sorted, normalized and never duplicated or fabricated',()=>{
  assert.deepEqual(normalizedChartData([{date:'20240102',equity:120},{date:'2024-01-01',equity:100}]),[{time:'2024-01-01',value:1},{time:'2024-01-02',value:1.2}]);
  assert.throws(()=>normalizedChartData([{date:'2024-02-31',equity:100}]));
  assert.throws(()=>normalizedChartData([{date:'20240101',equity:100},{date:'2024-01-01',equity:110}]));
  assert.throws(()=>normalizedChartData([{date:'2024-01-01',equity:NaN}]));
  assert.deepEqual(normalizedChartData([]),[]);
});
test('every financial chart uses one lazy engine with keyboard and tabular alternatives',()=>{
  const loader=readFileSync(new URL('../dist/chart-loader.mjs',import.meta.url),'utf8');
  const chart=readFileSync(new URL('../dist/chart-engine.mjs',import.meta.url),'utf8');
  assert.match(loader,/import\("\.\/chart-engine\.mjs"\)/);
  assert.match(chart,/renderMode: "richText"/);
  for(const name of ['charts.mjs','interactive-chart.mjs','experiment-comparison.mjs','portfolio-view.mjs'])
    assert.match(readFileSync(new URL('../dist/'+name,import.meta.url),'utf8'),/chart-loader\.mjs/);
  assert.match(chart,/createElement\("button"\)/);
  assert.match(chart,/createElement\("table"\)/);
});
