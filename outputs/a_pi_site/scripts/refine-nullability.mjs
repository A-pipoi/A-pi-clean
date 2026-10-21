import fs from 'node:fs';
import path from 'node:path';
import { patchChanges } from './patch-diff.mjs';
const updates = new Map();
function replace(file, before, after) {
 const old = updates.get(file) || fs.readFileSync(file,'utf8');
 if (!old.includes(before)) { console.error(`${file}: unmatched ${before}`); return; }
 updates.set(file,old.replaceAll(before,after));
}
replace('dist/charts.mjs','state?.pinned =','state.pinned =');
replace('dist/app.js','const daysByRange =','/** @type {Record<string,number>} */\n  const daysByRange =');
replace('dist/backtest-research-controls.mjs','const presets =','/** @type {Record<string,{lookback:number,top_n:number}>} */\n  const presets =');
replace('dist/backtest-runner.mjs',"if (button.dataset.running === 'true')", "if (!button || button.dataset.running === 'true')");
replace('dist/backtest-runner.mjs',"progress(statusData.status,", "progress(statusData.status || 'running',");
replace('dist/factor-mining-panel.mjs','  if (factorMiningWorking) return;', '  if (!(button instanceof HTMLElement) || factorMiningWorking) return;');
replace('dist/factor-mining-panel.mjs','item.factor_keys?.includes(candidate.factor_key)', "item.factor_keys?.includes(candidate.factor_key || '')");
replace('dist/factor-mining-panel.mjs','?.expression;', "?.expression || '';");
replace('dist/financial-statements-panel.mjs','meta.errors[0]', 'meta.errors?.[0]');
replace('dist/i18n.js','if (supportedLocales.has(stored))', 'if (stored && supportedLocales.has(stored))');
replace('dist/i18n.js','const marker =', '/** @type {string} */\n        const marker =');
replace('dist/locale-dictionary.js','const localeDictionary =', '/** @type {Record<string,Record<string,string>>} */\nconst localeDictionary =');
replace('dist/market-view.mjs','applyIndexCollection([index],', 'applyIndexCollection(index ? [index] : [],');
replace('dist/notification-center.mjs','let notificationItems = [];', "/** @type {import('../types/domain').Notification[]} */\nlet notificationItems = [];");
replace('dist/notification-center.mjs','const items = [];', "/** @type {import('../types/domain').Notification[]} */\n  const items = [];");
replace('dist/notification-center.mjs','const add = item =>', "/** @param {import('../types/domain').Notification} item */\n  const add = item =>");
replace('dist/stock-picker.mjs','const copy = {', '/** @type {Record<string,{loading:string;more:string;retry:string;complete:string}>} */\n  const copy = {');
replace('dist/stock-picker.mjs','refs.input.value =', 'if (!refs) return false;\n  refs.input.value =');
replace('dist/workspace-controller.mjs','  function renderWorkspaceCatalog(data)', "  /** @param {import('../types/domain').WorkspaceCatalog} data */\n  function renderWorkspaceCatalog(data)");
replace('dist/paper-page.mjs','exposure * 100', 'Number(exposure) * 100');
replace('dist/app-shell.mjs','clearTimeout(deps.toastTimer)', 'clearTimeout(deps.toastTimer ?? undefined)');
replace('dist/app-shell.mjs','deps.connectionState[key]', "deps.connectionState[/** @type {keyof import('../types/domain').AppState['connectionState']} */ (key)]");
replace('dist/app-shell.mjs','deps.restoreBacktestResult(button.dataset.backtestResult)', "deps.restoreBacktestResult(button.dataset.backtestResult || '')");
replace('dist/experiment-comparison.mjs',"const button = document.getElementById('compareExperimentsButton');", "const button = requiredElement('compareExperimentsButton');");
replace('dist/experiment-comparison.mjs','fetch(input.dataset.compareResult)', "fetch(input.dataset.compareResult || '')");
replace('dist/notification-center.mjs',"({ error: '!', warning: '!', success: '✓', info: 'i' }[item.type]", "(/** @type {Record<string,string>} */ ({ error: '!', warning: '!', success: '✓', info: 'i' })[item.type || 'info']");
let patch='*** Begin Patch\n';
for (const [file,text] of updates) patch+=`*** Update File: ${path.resolve(file).replaceAll('\\','/')}\n${patchChanges(fs.readFileSync(file,'utf8'),text)}`;
console.log(JSON.stringify(patch+'*** End Patch'));
