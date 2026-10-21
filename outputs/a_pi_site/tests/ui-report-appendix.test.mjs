import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {factorEvaluationReportMarkup} from '../dist/factor-mining-panel.mjs';
const read = name => readFileSync(new URL('../dist/'+name,import.meta.url),'utf8');
test('numeric tables have explicit aligned headers and cells without changing textual columns',()=>{
 const html=read('index.html');
 for(const label of ['权重','当前值','样本','数量','成交'])assert.ok(html.includes(`<th class="num">${label}</th>`));
 for(const name of ['portfolio-view.mjs','paper-page.mjs','factor-registry-view.mjs','experiment-comparison.mjs'])assert.match(read(name),/<td class="num/);
 assert.match(read('typography.css'),/table th\.num,\s*table td\.num\s*\{\s*text-align: right/);
 assert.match(read('app.js'),/minimumFractionDigits: digits,\s*maximumFractionDigits: digits/);
 const report=factorEvaluationReportMarkup({evaluation:{horizons:{'5':{mean_ic:0.1,icir:1.2}}}});
 assert.match(report,/<td class="num">0\.1000<\/td>/);
 assert.match(report,/<td class="num">1\.200<\/td>/);
 assert.doesNotMatch(report,/class=\\"/);
});
test('theme aliases and browser text surfaces share canonical semantic tokens',()=>{
 const tokens=read('design-tokens.css');
 for(const [alias,target] of [['foreground','text'],['secondary-text','muted'],['surface','panel'],['surface-raised','panel2'],['font-code','font-num']])assert.match(tokens,new RegExp('--'+alias+': var\\(--'+target+'\\)'));
 const styles=['styles.css','account-security.css','typography.css','theme.css'].map(read).join('\n');
 assert.equal((styles.match(/::selection\s*\{/g)||[]).length,1);
 assert.equal((styles.match(/::placeholder\s*\{/g)||[]).length,1);
 assert.match(read('theme.css'),/::placeholder\s*\{\s*color: var\(--faint\)/);
 assert.match(read('styles.css'),/\.factor-expression-area > label[^}]*color: var\(--text\)/);
});
test('financial data and decoration no longer masquerade as semantic emphasis',()=>{
 for(const name of ['index.html','paper-page.mjs','backtest-results.mjs','risk-view.js'])assert.doesNotMatch(read(name),/<(?:em|i)\b/);
 assert.match(read('market-view.mjs'),/changeElement\.className =\s*"datum-note "/);
 const css=read('typography.css');
 for(const selector of ['.research-stat strong','kbd,','.security-secrets pre,'])assert.ok(css.includes(selector));
 assert.match(read('theme.css'),/\.factor-mining-tag[^}]*min-width: 0/);
 assert.match(read('theme.css'),/\.chart-tooltip :is\(strong, span\)[^}]*white-space: normal[^}]*overflow-wrap: anywhere/);
});
