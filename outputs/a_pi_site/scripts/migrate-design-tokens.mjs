// One-time AST-directed mechanical migration, using the already installed parser.
import {readFile,writeFile} from 'node:fs/promises';
import {format} from 'prettier';
import postcss from 'prettier/plugins/postcss';
import {createHash} from 'node:crypto';
const files=['styles.css','account-security.css','typography.css','theme.css'];
if((await readFile(new URL('../dist/design-tokens.css',import.meta.url),'utf8')).includes('--paint-'))throw Error('One-time migration already applied; do not overwrite the canonical palette.');
const sizeToken=n=>n<=12?'xs':n<=13?'sm':n<=15?'base':n<=20?'md':n<=25?'lg':'xl';
const spaceToken=n=>{
 const rounded=Math.max(4,Math.round(Math.abs(n)/4)*4);
 const steps={4:1,8:2,12:3,16:4,20:5,24:6,32:8,40:10,48:12,64:16,80:20};
 const value=steps[rounded]?`var(--space-${steps[rounded]})`:`calc(var(--space-1) * ${rounded/4})`;
 return n<0?`calc(${value} * -1)`:value;
};
const paints=new Map();let removed=0,migrated=0;const reports=[];
const semantic={'#ffffff':'--panel','#fff':'--panel','#202433':'--text','#f7f8fa':'--bg','#596579':'--muted','#5b6678':'--faint','#6d5ce7':'--purple','#e9edf2':'--line'};
function transform(prop,value,selector){
 const old=value;
 if(prop==='font-size'){
  value=/auth-pitch h1|desktop-splash.*h1/.test(selector)?'var(--text-display)':value.replace(/(-?\d*\.?\d+)(px|rem)/g,(_,n,u)=>`var(--text-${sizeToken(Number(n)*(u==='rem'?16:1))})`);
  if(/clamp\(/.test(value))value=/h1/.test(selector)?'var(--text-lg)':'var(--text-base)';
 }
 if(prop==='font-weight'&&/^\d+$/.test(value))value=Number(value)>=600?'var(--weight-bold)':'var(--weight-normal)';
 if(prop==='font'){
  value=value.replace(/\b(400|500|600|650|700|750|800)\b/g,(_,n)=>Number(n)>=600?'var(--weight-bold)':'var(--weight-normal)');
  value=value.replace(/\b(\d*\.?\d+)(px|rem)/g,(_,n,u)=>`var(--text-${sizeToken(Number(n)*(u==='rem'?16:1))})`);
  value=value.replace(/\/\s*(\d*\.?\d+)/g,'/var(--leading-normal)');
 }
 if(prop==='line-height'&&!/^(normal|inherit|var\(|calc\()/.test(value))value=Number(value)<1.4?'var(--leading-tight)':'var(--leading-normal)';
 if(prop==='letter-spacing')value=/kicker|overline|eyebrow/.test(selector)?'var(--tracking-caps)':'normal';
 if(/^(gap|row-gap|column-gap|padding(?:-.+)?|margin(?:-.+)?)$/.test(prop))value=value.replace(/(-?\d*\.?\d+)(px|rem)\b/g,(_,n,u)=>Number(n)===0?'0':spaceToken(Number(n)*(u==='rem'?16:1)));
 if(!prop.startsWith('--'))value=value.replace(/#[\da-f]{3,8}\b/gi,color=>{
  const hex=color.toLowerCase();if(semantic[hex]&&!((hex==='#fff'||hex==='#ffffff')&&!prop.startsWith('background')))return `var(${semantic[hex]})`;
  const token=`--paint-${hex.slice(1)}`;paints.set(token,hex);return `var(${token})`;
 });
 if(!prop.startsWith('--'))value=value.replace(/rgba?\([^()]+\)/g,color=>{
  const token='--paint-'+createHash('sha256').update(color).digest('hex').slice(0,10);paints.set(token,color);return `var(${token})`;
 });
 if(value!==old)migrated++;return value;
}
for(const file of files){
 const url=new URL('../dist/'+file,import.meta.url),css=await readFile(url,'utf8');
 const ast=postcss.parsers.css.parse(css),seen=new Map(),dead=new Set();
 function inspect(nodes,context=''){
  for(const node of [...nodes].reverse()){
   if(node.type==='css-atrule'&&node.nodes)inspect(node.nodes,context+'/@'+node.name+':'+(node.params?.value||''));
   if(node.type!=='css-rule')continue;
   const selector=css.slice(node.source.start.offset,css.indexOf('{',node.source.start.offset)).trim();
   if(selector===':root'){dead.add(node);removed+=node.nodes.length;continue;}
   for(const decl of [...node.nodes].reverse()){
    if(decl.type!=='css-decl')continue;
    const key=context+'|'+selector+'|'+decl.prop,later=seen.get(key);
    if(later&&(!decl.important||later.important)){dead.add(decl);removed++;}else seen.set(key,decl);
   }
  }
 }
 inspect(ast.nodes);
 function serialize(node,selector=''){
  if(dead.has(node))return '';
  if(node.type==='css-decl')return `${node.prop}:${transform(node.prop,node.value.text,selector)}${node.important?' !important':''};`;
  if(node.nodes){
   const start=node.source.start.offset,brace=css.indexOf('{',start),head=css.slice(start,brace).trim();
   const content=node.nodes.map(n=>serialize(n,node.type==='css-rule'?head:selector)).join('\n');
   return content.trim()?head+'{'+content+'}':'';
  }
  return css.slice(node.source.start.offset,node.source.end.offset);
 }
 const result=await format(ast.nodes.map(n=>serialize(n)).join('\n'),{parser:'css'});
 await writeFile(url,result);reports.push({file,beforeBytes:css.length,afterBytes:result.length});
}
const tokenURL=new URL('../dist/design-tokens.css',import.meta.url);
let tokenCSS=await readFile(tokenURL,'utf8');
tokenCSS+='\n/* Component palette: literal colors occur only in this central token file. */\n:root{'+[...paints].sort().map(([k,v])=>`${k}:${v};`).join('\n')+'}\n';
await writeFile(tokenURL,await format(tokenCSS,{parser:'css'}));
const report={removedDeclarations:removed,migratedDeclarations:migrated,paletteTokens:paints.size,files:reports};
await writeFile(new URL('../design-token-migration.json',import.meta.url),JSON.stringify(report,null,2));
console.log(JSON.stringify(report,null,2));
