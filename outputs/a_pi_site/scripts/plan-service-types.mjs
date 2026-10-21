import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
const root=path.resolve('dist');
const files=fs.readdirSync(root).filter(name=>/\.(mjs|js)$/.test(name));
const sources=new Map(files.map(name=>[name,fs.readFileSync(path.join(root,name),'utf8')]));
const app=sources.get('app.js');
const stateKeys=new Set([...sources.get('app-state.mjs').matchAll(/^    (\w+):/gm)].map(match=>match[1]));
const owners=new Map();
for(const [name,source] of sources){for(const match of source.matchAll(/export (?:const|let) (APi\w+)/g))owners.set(match[1],name);for(const match of source.matchAll(/export function (create\w+)/g))owners.set(match[1],name);}
const contracts=new Map();
for(const [name,source] of sources){
 const tree=ts.createSourceFile(name,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
 function visit(node){
  if(ts.isCallExpression(node)&&node.arguments.length&&ts.isObjectLiteralExpression(node.arguments[0])){
   const callee=node.expression.getText(tree).replaceAll('?.','.');
   const key=callee.split('.')[0];
   if(owners.has(key)&&(!callee.includes('.')||callee.endsWith('.init')||callee.endsWith('.create'))){
    const module=owners.get(key); const typeName=key.replace(/^create|^APi/,'')+'Services';
    const fields=new Set();
    for(const property of node.arguments[0].properties){
     if(ts.isSpreadAssignment(property)){
      if(ts.isCallExpression(property.expression)&&property.expression.expression.getText(tree)==='createStatePort')for(const element of property.expression.arguments[1].elements)fields.add(element.text);
     }else if(property.name) fields.add(property.name.getText(tree));
    }
    contracts.set(module,{typeName,fields});
   }
  }
  ts.forEachChild(node,visit);
 }
 visit(tree);
}
let aliases=''; let patch='*** Begin Patch\n';
const existingServiceSource=fs.readFileSync('types/services.d.ts','utf8');
for(const [module,{typeName,fields}] of contracts){
 if(existingServiceSource.includes(`export type ${typeName} =`))continue;
 const states=[...fields].filter(name=>stateKeys.has(name)&&name!=='notificationState');
 const services=[...fields].filter(name=>!stateKeys.has(name)||name==='notificationState');
 aliases+=`export type ${typeName} = Pick<AppState, ${states.length?states.map(name=>JSON.stringify(name)).join(' | '):'never'}> & Pick<SharedServices, ${services.length?services.map(name=>JSON.stringify(name)).join(' | '):'never'}>;\n`;
 const source=sources.get(module); const lines=source.split(/\r?\n/);const edits=[];
 for(const line of lines){
  if(/(?:export )?function create\w+\(deps\)/.test(line)) edits.push([line,`/** @param {import('../types/services').${typeName}} deps */\n${line}`]);
  if(/(?:export )?function init\w+\(dependencies\)/.test(line)) edits.push([line,`/** @param {import('../types/services').${typeName}} dependencies */\n${line}`]);
  if(/^let services = null;/.test(line)||/^let deps = null;/.test(line)) {const varName=line.includes('services')?'services':'deps';edits.push([line,`/** @type {import('../types/services').${typeName}} */\nlet ${varName};`]);}
  if(/init\(deps\) \{/.test(line))edits.push([line,`  /** @param {import('../types/services').${typeName}} deps */\n${line}`]);
 }
 if(edits.length){patch+=`*** Update File: ${path.join(root,module).replaceAll('\\','/')}\n`;for(const [old,next] of edits)patch+='@@\n-'+old+'\n'+next.split('\n').map(line=>'+'+line).join('\n')+'\n';}
}
const serviceSource=fs.readFileSync('types/services.d.ts','utf8');
patch+=`*** Update File: ${path.resolve('types/services.d.ts').replaceAll('\\','/')}\n@@\n import type { AppState, BacktestResult, Budget, DataQuality, EquityPoint, FactorRow, Market, NotificationState, PaperState, Portfolio, Probe, RiskAlert, RiskLimits, Scheduler, Stock, Workspace, WorkspaceCatalog } from './domain';\n+\n`+aliases.trimEnd().split('\n').map(line=>'+'+line).join('\n')+'\n';
const tree=ts.createSourceFile('app.js',app,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS);
const serviceMethods=new Set([...serviceSource.matchAll(/^  (\w+)\(/gm)].map(match=>match[1]));
const functionLines=[];
for(const node of tree.statements)if(ts.isFunctionDeclaration(node)&&serviceMethods.has(node.name.text))functionLines.push([node.name.text,app.slice(node.getStart(tree),node.getStart(tree)+app.slice(node.getStart(tree)).indexOf('\n'))]);
const newFunctionLines=functionLines.filter(([name])=>!app.includes(`SharedServices['${name}']`));
if(newFunctionLines.length){patch+=`*** Update File: ${path.join(root,'app.js').replaceAll('\\','/')}\n`;for(const [name,line] of newFunctionLines)patch+=`@@\n+/** @type {import('../types/services').SharedServices['${name}']} */\n ${line}\n`;}
console.log(JSON.stringify({patch:patch+'*** End Patch',contracts:[...contracts.values()].map(value=>({name:value.typeName,fields:[...value.fields]}))}));
