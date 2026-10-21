import ts from 'typescript';
import {readFileSync, writeFileSync, readdirSync} from 'node:fs';
const root=new URL('../dist/',import.meta.url);
let count=0;
for(const name of readdirSync(root).filter(n=>/\.(?:js|mjs)$/.test(n)&&n!=='safe-html.mjs')){
 const url=new URL(name,root),source=readFileSync(url,'utf8');
 const ast=ts.createSourceFile(name,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
 const edits=[];const imports=new Set();
 function visit(node){
  if(ts.isBinaryExpression(node)&&node.operatorToken.kind===ts.SyntaxKind.EqualsToken&&
    ts.isPropertyAccessExpression(node.left)&&node.left.name.text==='innerHTML'){
   edits.push({start:node.getStart(ast),end:node.end,text:`setSafeHtml(${node.left.expression.getText(ast)}, ${node.right.getText(ast)})`});imports.add('setSafeHtml');
  }else if(ts.isCallExpression(node)&&ts.isPropertyAccessExpression(node.expression)&&node.expression.name.text==='insertAdjacentHTML'){
   if(node.arguments[0]?.getText(ast)!=='"beforeend"')throw new Error('Review unsupported insertAdjacentHTML position');
   edits.push({start:node.getStart(ast),end:node.end,text:`appendSafeHtml(${node.expression.expression.getText(ast)}, ${node.arguments[1].getText(ast)})`});imports.add('appendSafeHtml');
  }else ts.forEachChild(node,visit);
 }visit(ast);
 if(!edits.length)continue;
 let updated=source;for(const edit of edits.sort((a,b)=>b.start-a.start))updated=updated.slice(0,edit.start)+edit.text+updated.slice(edit.end);
 writeFileSync(url,`import { ${[...imports].join(', ')} } from "./safe-html.mjs";\n`+updated);count+=edits.length;
}
console.log(`Migrated ${count} unsafe HTML writes to the central DOMPurify fragment renderer.`);
