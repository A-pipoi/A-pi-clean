import ts from 'typescript';
import path from 'node:path';
const configPath=ts.findConfigFile('.',ts.sys.fileExists,'tsconfig.json');
const config=ts.readConfigFile(configPath,ts.sys.readFile);
const parsed=ts.parseJsonConfigFileContent(config.config,ts.sys,'.');
const program=ts.createProgram(parsed.fileNames,{...parsed.options,strict:true});
const diagnostics=ts.getPreEmitDiagnostics(program).map(diagnostic=>{
 const position=diagnostic.file?.getLineAndCharacterOfPosition(diagnostic.start??0);
 return {file:diagnostic.file?path.relative('.',diagnostic.file.fileName).replaceAll('\\','/'):null,line:position?position.line+1:null,column:position?position.character+1:null,code:diagnostic.code,message:ts.flattenDiagnosticMessageText(diagnostic.messageText,' ')};
});
console.log(JSON.stringify(diagnostics));
