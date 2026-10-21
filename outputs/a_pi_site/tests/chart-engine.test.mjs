import test from 'node:test';
import assert from 'node:assert/strict';
import {alignedChartSeries, financialChartOption, chartDate, repaintFinancialChart} from '../dist/chart-engine.mjs';
import {readFileSync} from 'node:fs';
test('theme repaint preserves current zoom and applies the new palette',()=>{
 const model={kind:'line',title:'QA',series:[{name:'A',points:[{date:'20240102',value:1}]}]};
 const zoom=[{type:'inside',start:23,end:61}];let applied;
 const chart={getOption:()=>({dataZoom:zoom}),setOption:value=>{applied=value}};
 const original=globalThis.document;
 globalThis.document={documentElement:{dataset:{theme:'dark'}}};
 try {repaintFinancialChart(chart,model);assert.deepEqual(applied.dataZoom,zoom);assert.equal(applied.textStyle.color,'#e0e8f5');}
 finally {globalThis.document=original;}
});
test('financial series align on real dates; missing values remain gaps',()=>{
 const result=alignedChartSeries([{name:'A',points:[{date:'20240103',value:1.1},{date:'20240101',value:1}]},{name:'B',points:[{date:'20240102',value:-.2}]}]);
 assert.deepEqual(result.dates,['2024-01-01','2024-01-02','2024-01-03']);
 assert.deepEqual(result.series[0].data,[1,null,1.1]);assert.deepEqual(result.series[1].data,[null,-.2,null]);
 assert.throws(()=>alignedChartSeries([{name:'A',points:[{date:'20240101',value:1},{date:'2024-01-01',value:2}]}]));
 assert.throws(()=>chartDate('20240231'));
});
test('validation shading clips to observed dates and never invents sealed observations',()=>{
 const option=financialChartOption({kind:'line',title:'QA',series:[{name:'A',points:[{date:'20240102',value:1},{date:'20240103',value:1.1}]}],bands:[{start:'20240101',end:'20240103',color:'blue'},{start:'20240201',end:'20240204',color:'red'}]});
 assert.equal(option.tooltip.renderMode,'richText');
 assert.equal(option.series[0].connectNulls,false);
 assert.equal(option.series[0].markArea.data.length,1);
 assert.equal(option.series[0].markArea.data[0][0].xAxis,'2024-01-02');
});
test('pie uses the same engine and excludes invalid or negative values',()=>{
 const option=financialChartOption({kind:'pie',title:'QA',segments:[{name:'cash',value:3},{name:'bad',value:NaN},{name:'bad2',value:-1}]});
 assert.equal(option.series[0].type,'pie');assert.equal(option.series[0].data.length,1);
 assert.equal(option.tooltip.show,false);
 assert.deepEqual(option.series[0].center,['50%','50%']);
 assert.equal(option.series[0].emphasis.scale,false);
 assert.equal(option.series[0].emphasis.label.show,false);
});
test('financial chart controls and data disclosure are centered with concise label',()=>{
 const css=readFileSync(new URL('../dist/styles.css',import.meta.url),'utf8');
 const engine=readFileSync(new URL('../dist/chart-engine.mjs',import.meta.url),'utf8');
 assert.match(css,/\.financial-chart-controls\s*\{[^}]*justify-content:\s*center/s);
 assert.match(css,/\.financial-chart details > summary\s*\{[^}]*text-align:\s*center;[^}]*list-style-position:\s*inside/s);
 assert.match(engine,/summary\.textContent\s*=\s*"查看图表数据"/);
 assert.doesNotMatch(engine,/查看图表数据（键盘可读）/);
});
