import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = name => readFileSync(new URL('../'+name,import.meta.url),'utf8');
test('reference theme is bundled last without changing business navigation',()=>{
  const html=read('dist/index.html'),build=read('scripts/build.mjs');
  assert.match(html,/href="reference-theme.css"/);
  assert.match(build,/reference-theme\.css/);
  for(const id of ['overview','research','factorMining','backtest','risk','paper']) assert.ok(html.includes('id="'+id+'"'));
});
test('theme keeps rounded sidebar, reduced motion and original brand',()=>{
  const css=read('dist/reference-theme.css');
  assert.match(css,/\.app-shell \.sidebar\s*\{[^}]*border-radius:\s*var\(--radius\)/s);
  assert.match(css,/prefers-reduced-motion/);
  assert.match(css,/prefers-reduced-transparency/);
  assert.match(css,/img\.brand-icon/);
  const logo=read('dist/a-pi-mark.svg');
  assert.match(logo, /<svg[^>]*viewBox="0 0 64 64"/);
  assert.doesNotMatch(logo, /<image|href=|script|Kuromi|库洛米/i);
});
test('dark world stays midnight navy and analytical charts consume semantic tokens',()=>{
  const tokens=read('dist/design-tokens.css'),charts=read('dist/chart-engine.mjs');
  assert.match(tokens,/#080e1c/i);
  for(const token of ['--chart-primary','--chart-secondary','--chart-grid','--summary-gradient','--warning-surface','--info-surface']) assert.ok(tokens.includes(token));
  assert.ok(charts.includes('--chart-primary'));
  assert.ok(charts.includes('--chart-grid'));
});
test('dark operational notices use semantic theme surfaces instead of legacy light paints',()=>{
  const css=read('dist/reference-theme.css');
  assert.match(css,/\.factor-pipeline-status\s*\{[^}]*background:\s*var\(--panel2\)/s);
  assert.match(css,/\.alert-card\.warning-alert\s*\{[^}]*background:\s*var\(--warning-surface\)/s);
  assert.match(css,/html\[data-theme="dark"\][^{]*\.alert-card[^}]*color:\s*var\(--text\)/s);
});
test('desktop sidebar uses compact proportions while mobile navigation stays touchable',()=>{
  const css=read('dist/reference-theme.css');
  assert.match(css,/\.app-shell \.sidebar\s*\{[^}]*width:\s*88px;[^}]*flex:\s*0 0 88px/s);
  assert.match(css,/\.app-shell \.nav-list\s*\{[^}]*gap:\s*var\(--space-2\);[^}]*margin-block-start:\s*var\(--space-4\)/s);
  assert.match(css,/\.app-shell \.nav-item\s*\{[^}]*justify-content:\s*center;[^}]*min-height:\s*52px/s);
  assert.match(css,/\.app-shell \.sidebar-footer\s*\{[^}]*flex-direction:\s*column/s);
  assert.match(css,/@media \(max-width:\s*720px\)[\s\S]*?\.app-shell \.nav-item\s*\{[^}]*min-height:\s*44px/);
});
test('icon-only navigation keeps accessible names, removes workspace chip, and exposes reminder count without a bubble',()=>{
  const html=read('dist/index.html'),css=read('dist/reference-theme.css');
  for(const [section,label] of [['overview','组合总览'],['research','因子研究'],['factorMining','因子挖掘'],['backtest','回测实验'],['risk','风险控制'],['paper','纸面交易']]) {
    assert.match(html,new RegExp(`<button class="nav-item[^>]*data-section="${section}"[^>]*aria-label="${label}"`));
  }
  assert.match(html,/<div class="workspace-selector" hidden>/);
  assert.match(html,/<button class="nav-item" data-section="risk"[^>]*><span class="nav-icon" aria-hidden="true"><svg[^>]*><path d="M12 22s8-4 8-11V5l-8-3-8 3v6c0 7 8 11 8 11Z"><\/path><\/svg><\/span>/);
  assert.match(html,/<span class="nav-badge" id="riskNavBadge" aria-hidden="true" hidden>0<\/span>/);
  assert.match(css,/\.app-shell \.nav-item \.nav-badge\s*\{[^}]*position:\s*absolute;[^}]*border-radius:\s*0;[^}]*background:\s*transparent/s);
  assert.match(read('dist/risk-view.js'),/deps\.localeText\("待处理提醒"\)/);
});
test('topbar overlay layer keeps notification center above dashboard cards',()=>{
  const css=read('dist/reference-theme.css');
  assert.match(css,/\.app-shell \.topbar\s*\{[^}]*position:\s*relative;[^}]*z-index:\s*200;/s);
  assert.match(read('dist/styles.css'),/\.notification-center\s*\{[^}]*z-index:\s*100;/s);
});
test('IC horizon selector uses the same pill-shaped outline as its refresh button',()=>{
  const css=read('dist/styles.css');
  assert.match(css,/#liveICPanel select\s*\{[^}]*border:\s*1px solid var\(--line\);[^}]*border-radius:\s*var\(--control-radius\)/s);
  assert.match(read('dist/reference-theme.css'),/\.app-shell \.secondary-button\s*\{[^}]*background:\s*var\(--panel2\)/s);
});
test('factor registry is separated from the prospective IC monitor panel',()=>{
  const css=read('dist/reference-theme.css');
  assert.match(css,/\.app-shell #liveICPanel \+ \.factor-panel\s*\{[^}]*margin-block-start:\s*var\(--space-4\)/s);
});
test('module surfaces use restrained light and dark gradient roles',()=>{
  const tokens=read('dist/design-tokens.css'),css=read('dist/reference-theme.css');
  assert.match(tokens,/--module-gradient:\s*linear-gradient\(122deg,#f0fbf6/);
  assert.match(tokens,/html\[data-theme="dark"\][\s\S]*?--module-gradient:\s*linear-gradient\(122deg,#222f3b/);
  assert.match(css,/\.app-shell \.metric-card:nth-child\(4\)\s*\{[^}]*background:\s*var\(--panel\)/s);
  assert.match(css,/\.app-shell :is\(\.panel,\.config-panel,\.result-panel,\.research-stat,\.paper-kpi,\.statement-card,[\s\S]*?background:\s*var\(--module-gradient\)/);
  assert.match(css,/\.app-shell \.overview-sidebar \.side-panel\s*\{[^}]*background:\s*var\(--module-gradient-cool\)/s);
  for(const family of ['lilac','mint','blue','rose']) assert.match(tokens,new RegExp('--module-gradient-'+family+':\\s*linear-gradient'));
  assert.match(css,/\.performance-panel,[\s\S]*?\.optimizer-panel\s*\{\s*--module-gradient: var\(--module-gradient-lilac\);/);
  assert.match(css,/\.allocation-panel,[\s\S]*?\.risk-overview\s*\{\s*--module-gradient: var\(--module-gradient-mint\);/);
  assert.match(css,/\.holdings-panel,[\s\S]*?\.live-ic-panel\s*\{\s*--module-gradient: var\(--module-gradient-blue\);/);
  assert.match(css,/\.audit-panel,[\s\S]*?\.rulebook-editor\s*\{\s*--module-gradient: var\(--module-gradient-rose\);/);
  for(const selector of ['.backtest-results','.orders-panel']) assert.ok(css.includes(selector));
});
test('table headers are centered without changing body cell alignment',()=>{
  const css=read('dist/reference-theme.css');
  assert.match(css,/\.app-shell table thead th\s*\{\s*text-align:\s*center;\s*\}/);
  assert.match(css,/\.app-shell :is\(\.table-wrap,\.table-scroll,\.holdings-table-wrap,\.paper-orders-table-wrap\) > table\s*\{\s*margin-top:\s*0;\s*\}/);
  assert.doesNotMatch(css,/\.app-shell table tbody td\s*\{[^}]*text-align:\s*center/s);
});
test('topbar status and account controls share a 44px height while keeping independent widths',()=>{
  const css=read('dist/reference-theme.css');
  assert.match(css,/\.app-shell \.topbar :is\(\.service-status,\.notification-button,\.user-chip,\.theme-control select\)\s*\{[^}]*height:\s*44px;[^}]*min-height:\s*44px/s);
  assert.match(css,/\.app-shell \.topbar \.service-status\s*\{[^}]*flex:\s*0 0 auto;[^}]*width:\s*max-content;[^}]*max-width:\s*100%;[^}]*min-width:\s*0/s);
  assert.match(css,/\.app-shell \.topbar \.notification-button\s*\{\s*width:\s*44px;\s*\}/);
  assert.doesNotMatch(css,/\.app-shell \.topbar :is\([^}]+\)\s*\{[^}]*width:/s);
});
test('restore actions fill their container without forcing the sensitivity link full width',()=>{
  const css=read('dist/styles.css');
  assert.match(css,/\.config-panel \.secondary-button\.full\s*\{[^}]*box-sizing:\s*border-box;[^}]*width:\s*100%;[^}]*max-width:\s*100%/s);
  assert.match(read('dist/index.html'),/class="[^"]*config-sensitivity-link[^"]*" id="sensitivityButton"/);
  assert.doesNotMatch(css,/FORCE_FULL_WIDTH_REMOVED/);
});
test('backtest cards and governance section have clear separation',()=>{
  const css=read('dist/reference-theme.css');
  assert.match(css,/\.app-shell #backtest \.backtest-layout\s*\{[^}]*column-gap:\s*var\(--space-4\);[^}]*margin-block-end:\s*var\(--space-4\)/s);
});
test('login language and theme selectors share aligned columns, dimensions and typography',()=>{
  const css=read('dist/reference-theme.css');
  assert.match(css,/\.auth-card-footer :is\(\.language-control,\.theme-control\)\s*\{[^}]*display:\s*grid;[^}]*grid-template-columns:\s*minmax\(0,\s*1fr\)\s*minmax\(0,\s*160px\);[^}]*flex:\s*0 0 100%;[^}]*width:\s*100%/s);
  assert.match(css,/\.auth-card-footer :is\(\.language-control select,\.theme-control select\)\s*\{[^}]*width:\s*100%;[^}]*height:\s*40px;[^}]*min-height:\s*40px;[^}]*font:\s*var\(--weight-bold\) var\(--type-meta\) var\(--font-ui\)/s);
});
test('login brand wordmark and Kuromi mark use an enlarged matching scale',()=>{
  const css=read('dist/styles.css');
  assert.match(css,/\.auth-brand\s*\{[^}]*font-size:\s*var\(--text-xl\)/s);
  assert.match(css,/\.auth-brand \.brand-mark\s*\{[^}]*width:\s*52px;[^}]*height:\s*52px;[^}]*font-size:\s*var\(--text-xl\)/s);
});
