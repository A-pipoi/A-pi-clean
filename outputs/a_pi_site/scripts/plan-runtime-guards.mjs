import fs from 'node:fs';
import path from 'node:path';
import { patchChanges } from './patch-diff.mjs';
const root=path.resolve('dist');
const ids=new Set([...fs.readFileSync('types/dom-elements.generated.d.ts','utf8').matchAll(/^  "([^"]+)":/gm)].map(match=>match[1]));
let patch='*** Begin Patch\n';
for(const file of fs.readdirSync(root).filter(name=>/\.(js|mjs)$/.test(name)&&name!=='runtime-utils.mjs')){
 const old=fs.readFileSync(path.join(root,file),'utf8');let updated=old;const helpers=new Set();
 if(file!=='i18n.js')updated=updated.replace(/document\.getElementById\((['"])([^'"]+)\1\)/g,(expression,quote,id)=>{
  if(!ids.has(id))return expression;
  helpers.add('requiredElement');return `requiredElement(${quote}${id}${quote})`;
 });
 updated=updated.replace(/catch \((\w+)\) \{/g,(_,name)=>{helpers.add('toError');return `catch (${name}Caught) {\n      const ${name} = toError(${name}Caught);`;});
 if(updated===old)continue;
 if(helpers.size)updated=`import { ${[...helpers].sort().join(', ')} } from './runtime-utils.mjs';\n`+updated;
 patch+=`*** Update File: ${path.join(root,file).replaceAll('\\','/')}\n`+patchChanges(old,updated);
}
console.log(JSON.stringify({patch:patch+'*** End Patch'}));
