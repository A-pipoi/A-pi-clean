import {readFile,writeFile} from 'node:fs/promises';
import {format} from 'prettier';
import postcss from 'prettier/plugins/postcss';
const url=new URL('../dist/theme.css',import.meta.url);
let css=await readFile(url,'utf8'),dark=new Map();const ast=postcss.parsers.css.parse(css),cuts=[];
for(const rule of ast.nodes){
 if(rule.type!=='css-rule')continue;
 const selector=css.slice(rule.source.start.offset,css.indexOf('{',rule.source.start.offset)).trim();
 if(selector!=='html[data-theme="dark"]')continue;
 for(const d of rule.nodes)if(d.type==='css-decl'&&d.prop.startsWith('--')){
  dark.set(d.prop,d.value.text);cuts.push([d.source.start.offset,d.source.end.offset]);
 }
}
for(const [start,end] of cuts.sort((a,b)=>b[0]-a[0]))css=css.slice(0,start)+css.slice(end);
css=css.replace(/html\[data-theme="dark"\]\s*\{\s*\}/g,'');
await writeFile(url,await format(css,{parser:'css'}));
const tokens=new URL('../dist/design-tokens.css',import.meta.url);
await writeFile(tokens,await format((await readFile(tokens,'utf8'))+'\nhtml[data-theme="dark"]{'+[...dark].map(([p,v])=>p+':'+v+';').join('')+'}',{parser:'css'}));
const comparison=new URL('../dist/experiment-comparison.mjs',import.meta.url);
await writeFile(comparison,(await readFile(comparison,'utf8')).replace(/font-size="(?:10|11)"/g,'style="font-size:var(--text-xs)"'));
for(const name of ['styles.css','account-security.css','typography.css','theme.css']){
 const path=new URL('../dist/'+name,import.meta.url);
 await writeFile(path,await format(await readFile(path,'utf8'),{parser:'css'}));
}
