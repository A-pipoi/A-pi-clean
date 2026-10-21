import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
const css=readFileSync(new URL('../dist/workbench-layers.css',import.meta.url),'utf8');

test('layered presentation is scoped to the workbench and preserves branding and finance truth',()=>{
 assert.doesNotMatch(css,/\.auth-|\.desktop-splash|\.brand-mark|content:\s*["']|url\(/);
 assert.doesNotMatch(css,/\.metric-value[^}]*animation|\.metric-value[^}]*transform|transition:\s*all/);
 assert.match(css,/\.app-shell \.metric-card\.primary-metric/);
 assert.match(css,/\.app-shell \.page-heading > div:first-child/);
 assert.match(css,/\.app-shell \.research-paper-panel \.heading-actions/);
});
test('motion is pointer-gated, brief, interruptible and removable',()=>{
 assert.match(css,/hover: hover.*pointer: fine.*prefers-reduced-motion: no-preference/);
 assert.match(css,/--desk-feedback: 160ms/);
 assert.match(css,/prefers-reduced-motion: reduce/);
 assert.match(css,/not\(:focus-visible\)/);
 assert.doesNotMatch(css,/@keyframes|infinite|backdrop-filter/);
});
test('source and immutable production bundle both include the same presentation layer',()=>{
 const html=readFileSync(new URL('../dist/index.html',import.meta.url),'utf8');
 const build=readFileSync(new URL('../scripts/build.mjs',import.meta.url),'utf8');
 assert.match(html,/href="workbench-layers\.css"/);
 assert.match(build,/'theme\.css','workbench-layers\.css'/);
 assert.match(build,/theme\|workbench-layers/);
 assert.match(css,/max-width: 1280px/);
 assert.match(css,/max-width: 440px/);
});
test('overview welcome copy starts at the content edge without the decorative arrow slot',()=>{
 const html=readFileSync(new URL('../dist/index.html',import.meta.url),'utf8');
 const styles=readFileSync(new URL('../dist/styles.css',import.meta.url),'utf8');
 assert.match(html,/class="welcome-copy">\s*<div>/);
 assert.doesNotMatch(html,/class="welcome-mark"/);
 assert.doesNotMatch(styles,/\.welcome-mark\s*\{/);
});
test('allocation ring shares square geometry with its plot and does not crowd the center',()=>{
 assert.match(css,/\.app-shell \.allocation-donut\s*\{[^}]*aspect-ratio: 1/);
 assert.match(css,/\.app-shell \.donut-center span\s*\{[^}]*margin: 0/);
 const styles=readFileSync(new URL('../dist/styles.css',import.meta.url),'utf8');
 assert.match(styles,/\.financial-chart-pie\s*\{[^}]*min-height: 0/);
});
