import { readFile, writeFile } from 'node:fs/promises';
const root = new URL('../dist/', import.meta.url);
const ticker = new URL('market-ticker.mjs', root);
let code = await readFile(ticker,'utf8');
code = code.replace(/const VISIBLE_COUNT = 10;\s*\/\*\* @type \{string\[\]\} \*\/\s*let previousSelection = \[\];/, '');
const start=code.indexOf('export function selectRandomMarketTickerQuotes(');
const end=code.indexOf('\nfunction updateMarketTickerMotion', start);
if(start<0||end<0)throw Error('Ticker migration target missing');
code=code.slice(0,start)+`export function selectStableMarketTickerQuotes(quotes) {
  const priority = Object.keys(TICKER_NAMES);
  const seen = new Map();
  for (const quote of quotes) {
    const code = String(quote.ts_code || "").toUpperCase();
    if (code && !seen.has(code)) seen.set(code, quote);
  }
  return [...seen.values()].sort((a,b) => {
    const aa=String(a.ts_code).toUpperCase(), bb=String(b.ts_code).toUpperCase();
    const ar=priority.indexOf(aa), br=priority.indexOf(bb);
    return (ar<0?priority.length:ar)-(br<0?priority.length:br) || aa.localeCompare(bb, "en");
  });
}
`+code.slice(end);
code=code.replaceAll('selectRandomMarketTickerQuotes','selectStableMarketTickerQuotes');
await writeFile(ticker,code);
const workspace=new URL('workspace-controller.mjs', root);
code=await readFile(workspace,'utf8');
let from=code.indexOf('      let data;\n      if (response.status === 404)');
let to=code.indexOf('      deps.workspaceCatalog = data;',from);
if(from<0||to<0)throw Error('Workspace fallback migration target missing');
code=code.slice(0,from)+`      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "HTTP " + response.status);
`+code.slice(to);
from=code.indexOf('      let data;\n      if (response.status === 404)');
to=code.indexOf('      const current = data.workspace || selected;',from);
if(from<0||to<0)throw Error('Workspace selection fallback target missing');
code=code.slice(0,from)+`      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || "HTTP " + response.status);
`+code.slice(to);
await writeFile(workspace,code);
const htmlFile=new URL('index.html',root);
code=await readFile(htmlFile,'utf8');
code=code.replace('aria-label="主要指数实时行情" aria-live="polite"','aria-label="主要指数实时行情" aria-live="off"');
code=code.replace('id="workspaceSwitcher" type="button"','id="workspaceSwitcher" type="button" disabled');
await writeFile(htmlFile,code);
