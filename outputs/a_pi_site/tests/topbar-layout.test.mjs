import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname,join} from 'node:path';
test('topbar search reserves normal flow space and long status can reflow',()=>{
 const styles=readFileSync(new URL('../dist/styles.css',import.meta.url),'utf8');
 const theme=readFileSync(new URL('../dist/theme.css',import.meta.url),'utf8');
 assert.match(styles,/\.topbar \.global-search\s*\{\s*position:\s*relative/);
 assert.doesNotMatch(styles,/\.topbar \.global-search\s*\{\s*position:\s*absolute/);
 assert.match(theme,/\.topbar\s*\{[^}]*height:\s*auto[^}]*flex-wrap:\s*wrap/);
 assert.match(theme,/\.topbar \.service-status\s*\{[^}]*overflow-wrap:\s*anywhere/);
});
test('service status shows a concise connection-details label while preserving live status for assistive text and tooltip',()=>{
 const root=join(dirname(fileURLToPath(import.meta.url)),'..','dist');
 const html=readFileSync(join(root,'index.html'),'utf8');
 const presenter=readFileSync(join(root,'workspace-presenter.mjs'),'utf8');
 assert.match(html,/id="servicePill"[^>]*><span class="pulse-dot"><\/span>连接详情<\/div>/);
 assert.match(presenter,/pill\.textContent = deps\.localeText\("连接详情"\)/);
 assert.match(presenter,/pill\.setAttribute\("aria-label", `Python 在线 · \$\{label\}`\)/);
 assert.match(presenter,/deps\.workspaceBuilding && "工作区生成中"/);
});
test('notification items have consistent separation inside the scrollable list',()=>{
 const css=readFileSync(new URL('../dist/reference-theme.css',import.meta.url),'utf8');
 assert.match(css,/\.app-shell \.notification-list\s*\{[^}]*display:\s*flex;[^}]*flex-direction:\s*column;[^}]*gap:\s*var\(--space-2\)/s);
 assert.match(css,/\.app-shell \.notification-item\s*\{[^}]*flex:\s*none;[^}]*border:\s*1px solid var\(--line\)/s);
});
