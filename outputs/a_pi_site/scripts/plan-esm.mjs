import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

const root = path.resolve('dist');
const entries = fs.readdirSync(root).filter(name => /\.(js|mjs)$/.test(name));
const owners = new Map();
const sources = new Map(entries.map(name => [name, fs.readFileSync(path.join(root, name), 'utf8')]));
for (const [name, source] of sources) {
  const tree = ts.createSourceFile(name, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.JS);
  const edits = [];
  const visit = node => {
    if (ts.isBinaryExpression(node) && node.operatorToken.kind === ts.SyntaxKind.EqualsToken && /^window\.APi\w+$/.test(node.left.getText(tree))) {
      const api = node.left.getText(tree).slice(7);
      owners.set(api, name);
      let top = node;
      while (top.parent && top.parent !== tree) top = top.parent;
      if (source.trimStart().startsWith('(() =>')) {
        edits.push({ start: node.parent.getStart(tree), end: node.parent.end, text: `return ${node.right.getText(tree)};` });
        edits.push({ start: 0, end: 0, text: `export const ${api} = ` });
      } else {
        const statement = ts.isIfStatement(top) ? top : node.parent;
        edits.push({ start: statement.getStart(tree), end: statement.end, text: `export const ${api} = ${node.right.getText(tree)};` });
      }
    }
    ts.forEachChild(node, visit);
  };
  visit(tree);
  let updated = source;
  for (const edit of edits.sort((a,b)=>b.start-a.start)) updated = updated.slice(0,edit.start) + edit.text + updated.slice(edit.end);
  sources.set(name, updated);
}
owners.set('APiResearchControls', 'backtest-research-controls.mjs');
sources.set('backtest-research-controls.mjs', 'export let APiResearchControls = null;\n' + sources.get('backtest-research-controls.mjs').replace('globalThis.window && (globalThis.window.APiResearchControls = controls);', 'APiResearchControls = controls;'));
let patch = '*** Begin Patch\n';
function changes(oldText, newText) {
  const left = oldText.trimEnd().split(/\r?\n/);
  const right = newText.trimEnd().split(/\r?\n/);
  const matrix = Array.from({length:left.length+1},()=>new Uint32Array(right.length+1));
  for(let i=left.length-1;i>=0;i--) for(let j=right.length-1;j>=0;j--) matrix[i][j]=left[i]===right[j]?matrix[i+1][j+1]+1:Math.max(matrix[i+1][j],matrix[i][j+1]);
  const operations=[];
  let i=0,j=0;
  while(i<left.length||j<right.length){
    if(i<left.length&&j<right.length&&left[i]===right[j]){operations.push(' '+left[i++]);j++;}
    else if(j<right.length&&(i===left.length||matrix[i][j+1]>=matrix[i+1][j])) operations.push('+'+right[j++]);
    else operations.push('-'+left[i++]);
  }
  const intervals=[];
  for(let index=0;index<operations.length;index++) if(operations[index][0]!==' '){
    const start=Math.max(0,index-3),end=Math.min(operations.length,index+4);
    const previous=intervals.at(-1);
    if(previous&&start<=previous[1])previous[1]=end;else intervals.push([start,end]);
  }
  return intervals.map(([start,end])=>'@@\n'+operations.slice(start,end).join('\n')+'\n').join('');
}
for (const [name, source] of sources) {
  const imports = new Set();
  let updated = source.replace(/(?:globalThis\.)?window\.(APi\w+)/g, (_,api) => {
    if (owners.get(api) !== name) imports.add(api);
    return api;
  });
  updated = [...imports].sort().map(api=>`import { ${api} } from './${owners.get(api)}';`).join('\n') + (imports.size ? '\n' : '') + updated;
  if (name === 'app.js') updated = "import './feature-store-panel.mjs';\n" + updated;
  const old = fs.readFileSync(path.join(root,name),'utf8');
  if (updated === old) continue;
  patch += `*** Update File: ${path.join(root,name).replaceAll('\\','/')}\n` + changes(old, updated);
}
const html = fs.readFileSync(path.join(root,'index.html'),'utf8');
const oldScripts = html.slice(html.indexOf('<script src="i18n.js'), html.indexOf('</body>')).trimEnd();
patch += `*** Update File: ${path.join(root,'index.html').replaceAll('\\','/')}\n@@\n` + oldScripts.split(/\r?\n/).map(line=>'-'+line).join('\n') + '\n+    <script type="module" src="app.js?v=20261001-contracts-1"></script>\n';
console.log(JSON.stringify({patch:patch+'*** End Patch', owners:Object.fromEntries(owners)}));
