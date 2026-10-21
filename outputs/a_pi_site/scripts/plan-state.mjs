import fs from 'node:fs';
import path from 'node:path';
import ts from 'typescript';

const appPath = path.resolve('dist/app.js');
const program = ts.createProgram([appPath], { allowJs:true, checkJs:true, target:ts.ScriptTarget.ES2022, module:ts.ModuleKind.ESNext, moduleResolution:ts.ModuleResolutionKind.Bundler });
const checker = program.getTypeChecker();
const tree = program.getSourceFile(appPath);
const fields = new Map();
const edits=[];
for(const statement of tree.statements) {
  if(!ts.isVariableStatement(statement))continue;
  const declarations=statement.declarationList.declarations;
  if(declarations.length!==1)continue;
  const declaration=declarations[0];
  if(!ts.isIdentifier(declaration.name))continue;
  const name=declaration.name.text;
  if(!(statement.declarationList.flags&ts.NodeFlags.Let)&&!['notificationState','connectionState','backtestRestoreRequests'].includes(name))continue;
  fields.set(checker.getSymbolAtLocation(declaration.name), {name, value:declaration.initializer?.getText(tree)||'null'});
  edits.push({start:statement.getStart(tree),end:statement.end,text:''});
}
function visit(node){
  if(ts.isIdentifier(node)){
    const field=fields.get(ts.isShorthandPropertyAssignment(node.parent)?checker.getShorthandAssignmentValueSymbol(node.parent):checker.getSymbolAtLocation(node));
    if(field&&!ts.isVariableDeclaration(node.parent)&&!ts.isPropertyAccessExpression(node.parent)){
      edits.push({start:node.getStart(tree),end:node.end,text:ts.isShorthandPropertyAssignment(node.parent)?`${field.name}: state.${field.name}`:`state.${field.name}`});
    }
    if(field&&ts.isPropertyAccessExpression(node.parent)&&node.parent.expression===node){
      edits.push({start:node.getStart(tree),end:node.end,text:`state.${field.name}`});
    }
  }
  ts.forEachChild(node,visit);
}
visit(tree);
let updated=tree.text;
for(const edit of edits.sort((a,b)=>b.start-a.start)) updated=updated.slice(0,edit.start)+edit.text+updated.slice(edit.end);
updated=updated.replace(/\n{3,}/g,'\n\n');
updated="import { createAppState, createStatePort } from './app-state.mjs';\n"+updated;
updated=updated.replace(/(const localeRuntime =[^\n]*\n)/, '$1const state = createAppState(localeRuntime.locale());\n');
updated=updated.replace(/(const \w+Runtime = create\w+\(\{)([\s\S]*?)(\n\}\);)/g,(_,opening,body,closing)=>{
  const keys=[];
  const next=body.replace(/\n  get (\w+)\(\) \{ return state\.\1; \},(?:\n  set \1\(value\) \{ state\.\1 = value; \},)?/g,(_,key)=>{keys.push(key);return '';});
  return opening+(keys.length?`\n  ...createStatePort(state, ${JSON.stringify(keys)}),`:'')+next+closing;
});
const stateSource=`/** State owned by one application instance, exposed through scoped dependency ports. */
export function createAppState(activeLocale) {
  return {
${[...fields.values()].map(({name,value})=>'    '+name+': '+(name==='activeLocale'?'activeLocale':value)+',').join('\n')}
  };
}

/**
 * @template {object} T
 * @template {keyof T} K
 * @param {T} state
 * @param {readonly K[]} keys
 * @returns {Pick<T, K>}
 */
export function createStatePort(state, keys) {
  const port = /** @type {Pick<T, K>} */ ({});
  for (const key of keys) Object.defineProperty(port, key, {
    enumerable: true, get: () => state[key], set: value => { state[key] = value; },
  });
  return port;
}
`;
// A whole-entry replacement is modest after prior module extraction; generated state is a new module.
console.log(JSON.stringify({patch:'*** Begin Patch\n*** Add File: '+path.resolve('dist/app-state.mjs').replaceAll('\\','/')+'\n'+stateSource.trimEnd().split('\n').map(line=>'+'+line).join('\n')+'\n*** Update File: '+appPath.replaceAll('\\','/')+'\n@@\n'+tree.text.trimEnd().split(/\r?\n/).map(line=>'-'+line).join('\n')+'\n'+updated.trimEnd().split(/\r?\n/).map(line=>'+'+line).join('\n')+'\n*** End Patch', fields:[...fields.values()].map(field=>field.name)}));
