// One-off mechanical migration of audited decorative tags and numeric columns.
import { readFileSync, writeFileSync } from 'node:fs';
import path from 'node:path';
const root = path.resolve(import.meta.dirname, '../dist');
if (readFileSync(path.join(root, 'index.html'), 'utf8').includes('class="datum-note'))
  throw new Error('One-off migration already applied; do not repeat it.');
const edit = (name, transform) => {
  const file = path.join(root, name);
  writeFileSync(file, transform(readFileSync(file, 'utf8')));
};
const span = (text, tag, hook) => text.replace(new RegExp(`<${tag}\\b([^>]*)>`, 'g'), (_, attrs) => {
  if (/\bclass="/.test(attrs)) attrs = attrs.replace(/class="/, `class="${hook} `);
  else attrs += ` class="${hook}"`;
  return `<span${attrs}>`;
}).replaceAll(`</${tag}>`, '</span>');
for (const name of ['index.html', 'paper-page.mjs', 'backtest-results.mjs', 'risk-view.js']) {
  edit(name, text => span(span(text, 'em', 'datum-note'), 'i', 'ui-decoration'));
}
for (const name of ['styles.css', 'typography.css']) {
  edit(name, text => text.replace(/\.auth-pitch h1 em/g, '.auth-pitch h1 __REAL_EM__')
    .replace(/\bem(?=[\s.,:{>])/g, '.datum-note')
    .replace(/(?<![-\w])i(?=[\s.,:{>])/g, '.ui-decoration')
    .replaceAll('__REAL_EM__', 'em'));
}
edit('index.html', text => text.replace('<th>权重</th>', '<th class="num">权重</th>')
  .replace('<th>当前值</th><th>样本</th>', '<th class="num">当前值</th><th class="num">样本</th>')
  .replace('<th>数量</th><th>成交</th>', '<th class="num">数量</th><th class="num">成交</th>'));
edit('portfolio-view.mjs', text => text.replace('<td>${hasPaper ?', '<td class="num">${hasPaper ?'));
edit('paper-page.mjs', text => text.replaceAll('<td>${services.formatNumber', '<td class="num">${services.formatNumber'));
edit('factor-registry-view.mjs', text => text.replace('<td class="${valueClass}">', '<td class="num ${valueClass}">')
  .replace('<td>${deps.escapeHtml(String(row.sample_days', '<td class="num">${deps.escapeHtml(String(row.sample_days'));
edit('experiment-comparison.mjs', text => text.replace('<th>${deps.escapeHtml(`#', '<th class="num">${deps.escapeHtml(`#')
  .replace('<td>${deps.escapeHtml(getter', '<td class="num">${deps.escapeHtml(getter'));
edit('factor-mining-panel.mjs', text => text.replace(/<td>/g, '<td class=\\"num\\">')
  .replace(/<th>(Rank IC|Normal IC|ICIR|月度胜率|多空收益|原始 p|DSR p|BH q|样本|p10|p50|p90|p50 年变动)<\/th>/g, '<th class="num">$1</th>'));
edit('backtest-results.mjs', text => text.replace('<td>${deps.formatPercent', '<td class="num">${deps.formatPercent')
  .replace('<td style="--corr-alpha:', '<td class="num" style="--corr-alpha:'));
// Drop superseded selection/placeholder palettes; theme.css owns both roles.
for (const name of ['styles.css', 'account-security.css', 'theme.css']) {
  edit(name, text => text.replace(/[^{}]*::(?:selection|placeholder)\s*\{[^{}]*\}/g, ''));
}
