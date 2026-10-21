/** Produce apply_patch changes for a function group; never write source files. */
import ts from 'typescript';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const groups = {
  'service-health': ['checkServiceHealth'],
  'market-view': ['applyIndexValue', 'applyIndexCollection', 'applyShanghaiIndex', 'loadMarketIndex', 'applyMarketSnapshot'],
  'portfolio-view': ['applyPortfolioSnapshot', 'applySignalSnapshot', 'renderAllocationDonut', 'applyRiskSnapshot'],
  'data-health': ['calculateDataHealth', 'renderDataHealth'],
  'workspace-presenter': ['renderGuardrails', 'applyWorkspaceSnapshot', 'renderDataProbeStatus'],
  'factor-registry-view': ['renderFactorTable', 'applyResearchSnapshot', 'loadFactorDefinitions'],
  'research-governance': ['loadGovernance', 'setStatusText', 'loadOperationsStatus', 'loadAdjustmentHealth'],
  'workspace-controller': ['loadWorkspace', 'scheduleWorkspaceRetry', 'setWorkspaceMenuOpen', 'renderWorkspaceCatalog', 'loadWorkspaceCatalog', 'selectWorkspace', 'initWorkspaceSelector'],
  'backtest-results': ['applyBacktestResult', 'restoreBacktestResult', 'renderMonthlyReturns', 'loadBenchmarkReports', 'renderBacktestRiskAnalytics'],
  'app-shell': ['enterWorkspace', 'persistSession', 'exitWorkspace', 'showSection', 'showToast', 'updateConnectionStatus', 'dialogRow', 'openRiskAlertDetails', 'openUtilityDialog', 'closeUtilityDialog', 'openHelpDialog', 'openConnectionDialog', 'openExposureDialog', 'openRunDetailsDialog'],
  'runtime-config': ['renderRuntimeConfig', 'loadRuntimeConfig', 'initBacktestUniverseSelector', 'syncResearchBudgetControls'],
};
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const appPath = path.join(root, 'dist', 'app.js');
const source = readFileSync(appPath, 'utf8');
const program = ts.createProgram([appPath], { allowJs: true, checkJs: true, target: ts.ScriptTarget.ES2022 });
const file = program.getSourceFile(appPath);
const checker = program.getTypeChecker();
const group = process.argv[2];
const names = groups[group];
if (!names) throw new Error('Unknown group ' + group);
const functions = file.statements.filter(node => ts.isFunctionDeclaration(node) && names.includes(node.name?.text));
if (functions.length !== names.length) throw new Error('Missing function');
const dependencies = new Map();
function topDeclaration(symbol) {
  return symbol?.declarations?.find(declaration => {
    if (declaration.getSourceFile() !== file) return false;
    if (ts.isFunctionDeclaration(declaration)) return declaration.parent === file;
    return ts.isVariableDeclaration(declaration) && declaration.parent?.parent?.parent === file;
  });
}
function identifierIsValue(node) {
  if (ts.isPropertyAccessExpression(node.parent) && node.parent.name === node) return false;
  if ((ts.isPropertyAssignment(node.parent) || ts.isMethodDeclaration(node.parent)) && node.parent.name === node) return false;
  if (ts.isVariableDeclaration(node.parent) && node.parent.name === node) return false;
  if (ts.isFunctionDeclaration(node.parent) && node.parent.name === node) return false;
  if (ts.isParameter(node.parent) && node.parent.name === node) return false;
  return true;
}
const extracted = [];
for (const fn of functions) {
  const edits = [];
  function visit(node) {
    if (ts.isIdentifier(node) && identifierIsValue(node)) {
      const symbol = ts.isShorthandPropertyAssignment(node.parent)
        ? checker.getShorthandAssignmentValueSymbol(node.parent)
        : checker.getSymbolAtLocation(node);
      const declaration = topDeclaration(symbol);
      if (declaration && !names.includes(node.text)) {
        dependencies.set(node.text, declaration);
        // Shorthand properties retain their public key after qualification.
        const replacement = ts.isShorthandPropertyAssignment(node.parent) ? node.text + ': deps.' + node.text : 'deps.' + node.text;
        edits.push({ start: node.getStart(file), end: node.end, replacement });
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(fn);
  let text = source.slice(fn.getStart(file), fn.end);
  for (const edit of edits.sort((a, b) => b.start - a.start)) {
    const start = edit.start - fn.getStart(file), end = edit.end - fn.getStart(file);
    text = text.slice(0, start) + edit.replacement + text.slice(end);
  }
  extracted.push(text);
}
const camel = group.split('-').map(part => part[0].toUpperCase() + part.slice(1)).join('');
const factory = 'create' + camel;
const runtime = group.split('-').map((part, index) => index ? part[0].toUpperCase() + part.slice(1) : part).join('') + 'Runtime';
const moduleSource = '/** ' + group.replaceAll('-', ' ') + ' with explicit services and live state accessors. */\nexport function ' + factory + '(deps) {\n' + extracted.map(text => text.split('\n').map(line => '  ' + line).join('\n')).join('\n\n') + '\n  return { ' + names.join(', ') + ' };\n}\n';
const properties = [];
for (const [name, declaration] of [...dependencies].sort(([a], [b]) => a.localeCompare(b))) {
  if (ts.isFunctionDeclaration(declaration)) properties.push('  ' + name + ',');
  else {
    properties.push('  get ' + name + '() { return ' + name + '; },');
    if (!(declaration.parent.flags & ts.NodeFlags.Const)) properties.push('  set ' + name + '(value) { ' + name + ' = value; },');
  }
}
const initialization = 'const ' + runtime + ' = ' + factory + '({\n' + properties.join('\n') + '\n});\n';
const wrappers = functions.map(fn => {
  const params = fn.parameters.map(parameter => {
    if (!ts.isIdentifier(parameter.name)) {
      // These groups currently only use a destructured parameter with an
      // existing default; preserve its values by reconstructing the object.
      if (ts.isObjectBindingPattern(parameter.name)) return '{ ' + parameter.name.elements.map(element => element.name.getText(file)).join(', ') + ' }';
      throw new Error('Unsupported parameter');
    }
    return (parameter.dotDotDotToken ? '...' : '') + parameter.name.text;
  });
  return source.slice(fn.getStart(file), fn.body.getStart(file)) + '{\n  return ' + runtime + '.' + fn.name.text + '(' + params.join(', ') + ');\n}';
});
let patch = '*** Begin Patch\n*** Add File: ' + path.join(root, 'dist', group + '.mjs') + '\n' + moduleSource.split('\n').filter((_, i, a) => i < a.length - 1).map(line => '+' + line).join('\n');
patch += '\n*** Update File: ' + appPath + '\n@@\n+import { ' + factory + ' } from "./' + group + '.mjs";\n const authView = document.getElementById(\'authView\');';
patch += '\n@@\n-function localeText(value) {\n' + initialization.split('\n').map(line => '+' + line).join('\n') + '\n+function localeText(value) {';
functions.forEach((fn, i) => {
  patch += '\n@@\n' + source.slice(fn.getStart(file), fn.end).split('\n').map(line => '-' + line.replace(/\r$/, '')).join('\n') + '\n' + wrappers[i].split('\n').map(line => '+' + line.replace(/\r$/, '')).join('\n');
});
patch += '\n*** End Patch';
console.log(JSON.stringify({ group, dependencies: [...dependencies.keys()], patch }));
