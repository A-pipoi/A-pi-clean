import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';
import { patchChanges } from './patch-diff.mjs';
const root=path.resolve('dist');
const serviceSource=fs.readFileSync('types/services.d.ts','utf8');
const serviceMethods=new Set([...serviceSource.matchAll(/(?:^|;)\s*(\w+)\(/gm)].map(match=>match[1]));
const domain=name=>`import('../types/domain').${name}`;
const general={email:'string',id:'string',message:'string',label:'string',title:'string',body:'string',value:'unknown',source:'unknown',status:'string',text:'string',symbol:'string',symbols:'string[]',tsCode:'string',workspaceId:'string',delaySeconds:'number',open:'boolean',valueId:'string',changeId:'string',canvas:'HTMLCanvasElement',equity:'number',marketValue:'number',index:'number',timeoutMs:'number',path:'string',jobId:'string',url:'string',busyLabel:'string',fallback:'number',digits:'number',dictionary:'Record<string, Record<string, string>>',storageKey:'string',node:'Node',element:'Element',root:'Node',prefix:'string',notificationState:domain('NotificationState'),paperStatusLabel:'(status: unknown) => string',paper:domain('PaperState')+' | null',portfolio:domain('Portfolio')+' | null',positions:domain('Stock')+'[]',signals:domain('Stock')+'[] | undefined',indices:domain('Stock')+'[]',curve:domain('EquityPoint')+'[]',market:domain('Market')+' | undefined',research:domain('ResearchSummary'),entries:'HTMLElement',host:'HTMLElement',current:domain('Stock')+'[]',incoming:domain('Stock')+'[]',seed:domain('Stock')+'[]',fallbackSymbols:'string[]',candidate:domain('FactorCandidate'),factor:domain('FactorRow'),run:domain('FactorCandidate'),query:'string',results:domain('BacktestResult')+'[]',onProgress:`(job: ${domain('BacktestJob')}) => void`,payload:'Record<string, unknown>',values:'number[]',optimization:'Record<string, unknown>',formatter:'(value: unknown) => string',metric:'string',limit:'number | null | undefined',number:'number | null',written:'number',total:'number',versions:'number',rows:'number'};
const functions={
 applyIndexValue:{index:domain('Stock')+' | undefined'},applyShanghaiIndex:{index:domain('Stock')+' | undefined'},
 applyWorkspaceSnapshot:{data:domain('Workspace')},renderWorkspaceSelector:{data:domain('WorkspaceCatalog')},
 applyBacktestResult:{data:domain('BacktestResult')},loadBenchmarkReports:{data:domain('BacktestResult')},
 renderGuardrails:{rows:domain('Audit')},applyResearchSnapshot:{research:domain('ResearchSummary')},
 setGate:{state:'string'},progress:{state:'string'},buildNotificationRecords:{notificationState:domain('NotificationState')},
 factorMiningSealedReportMarkup:{data:domain('FactorMiningSnapshot')},renderFactorMining:{data:domain('FactorMiningSnapshot')},
 renderFinancialStatements:{data:'Record<string, unknown>'},
};
let patch='*** Begin Patch\n';
for(const file of fs.readdirSync(root).filter(name=>/\.(js|mjs)$/.test(name)&&!['locale-dictionary.js','app-state.mjs','runtime-utils.mjs'].includes(name))){
 const old=fs.readFileSync(path.join(root,file),'utf8'),tree=ts.createSourceFile(file,old,ts.ScriptTarget.Latest,true,ts.ScriptKind.JS),edits=[];
 const visit=node=>{
  if(ts.isFunctionDeclaration(node)||ts.isMethodDeclaration(node)||ts.isArrowFunction(node)){
   let name=node.name?.getText(tree)|| (ts.isVariableDeclaration(node.parent)?node.parent.name.getText(tree):'');
   if(node.jsDoc?.length){ts.forEachChild(node,visit);return;}
   if(ts.isArrowFunction(node)&&!ts.isVariableDeclaration(node.parent)){ts.forEachChild(node,visit);return;}
   const params=[];
   for(const parameter of node.parameters){
    if(!ts.isIdentifier(parameter.name))continue;
    const key=parameter.name.text;
    let type=functions[name]?.[key]||general[key];
    if(key==='event') type=old.slice(node.getStart(tree),node.end).includes('event.client')?'MouseEvent':old.slice(node.getStart(tree),node.end).includes('event.key')?'KeyboardEvent':'Event';
    if(key==='deps'&&file==='risk-view.js')type="import('../types/services').RiskViewServices";
    if(key==='dependencies'&&file==='factor-registration.mjs')type="import('../types/services').FactorRegistrationServices";
    if(key==='services'&&file==='factor-mining-panel.mjs')type="import('../types/services').FactorMiningPanelServices";
    if(type)params.push(`@param {${type}} ${key}`);
   }
   if(ts.isFunctionDeclaration(node)&&serviceMethods.has(name)) {
    const options=ts.isFunctionDeclaration(node)&&node.parameters.find(param=>ts.isObjectBindingPattern(param.name));
    if(!options) {edits.push({start:node.getStart(tree),text:`/** @type {import('../types/services').SharedServices['${name}']} */\n`});params.length=0;}
   }
   if(params.length)edits.push({start:node.getStart(tree),text:`/** ${params.join(' ')} */\n`});
  }
  ts.forEachChild(node,visit);
 };
 visit(tree);
 let updated=old;
 for(const edit of edits.sort((a,b)=>b.start-a.start))updated=updated.slice(0,edit.start)+edit.text+updated.slice(edit.start);
 if(file==='factor-mining-panel.mjs')updated=updated.replace('let dependencies = null;',"/** @type {import('../types/services').FactorMiningPanelServices} */\nlet dependencies;");
 if(updated!==old)patch+=`*** Update File: ${path.join(root,file).replaceAll('\\','/')}\n`+patchChanges(old,updated);
}
console.log(JSON.stringify({patch:patch+'*** End Patch'}));
