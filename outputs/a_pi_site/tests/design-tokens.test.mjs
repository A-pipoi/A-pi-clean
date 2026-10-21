import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import postcss from 'prettier/plugins/postcss';
const read=name=>readFileSync(new URL('../dist/'+name,import.meta.url),'utf8');
test('one canonical light palette, six text roles and one shared local family',()=>{
 const tokens=read('design-tokens.css');
 assert.equal((tokens.match(/:root\s*\{/g)||[]).length,1);
 for(const role of ['xs','sm','base','md','lg','xl'])assert.match(tokens,new RegExp('--text-'+role+':'));
 assert.match(tokens,/html\[data-theme="dark"\]/);
 assert.match(tokens,/--font-ui:\s*"Microsoft YaHei UI"/);
 assert.match(tokens,/--font-num:\s*var\(--font-ui\)/);
 assert.match(tokens,/--font-code:\s*var\(--font-num\)/);
 assert.doesNotMatch(tokens,/Cascadia Code|JetBrains Mono|Consolas|Roboto Mono/);
 assert.doesNotMatch(tokens,/@font-face|https?:/);
});
test('component styles use tokens for scale, weight, leading and grid spacing',()=>{
 for(const name of ['styles.css','account-security.css','typography.css','theme.css','workbench-layers.css']){
  const css=read(name);assert.doesNotMatch(css,/:root\s*\{/);
  const ast=postcss.parsers.css.parse(css);
  function visit(nodes){for(const n of nodes){
   if(n.type==='css-decl'){
    const v=n.value.text;
    if(['font-size','font-weight','line-height','letter-spacing'].includes(n.prop))assert.doesNotMatch(v,/^-?[\d.]+(?:px|rem|em)?$/,name+' '+n.prop+' '+v);
    if(/^(gap|row-gap|column-gap|padding(?:-.+)?|margin(?:-.+)?)$/.test(n.prop))assert.doesNotMatch(v,/-?\d*\.?\d+px\b/,name+' '+n.prop+' '+v);
   }else if(n.nodes)visit(n.nodes);
  }}visit(ast.nodes);
 }
});
test('dynamic feature panel cannot override tokens and favicon uses desktop brand',()=>{
 assert.doesNotMatch(read('feature-store-panel.mjs'),/createElement\("style"\)|font-weight:650/);
 assert.match(read('index.html'),/rel="icon"[^>]*a-pi-mark\.svg/);
 assert.doesNotMatch(read('charts.mjs'),/ctx\.font = `\d+px/);
 assert.match(read('icons.mjs'),/"•••":/);
});
