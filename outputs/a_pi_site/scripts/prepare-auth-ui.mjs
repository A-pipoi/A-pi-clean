// Emit an apply_patch for the isolated release candidate, never overwrite live UI.
import fs from 'node:fs';
import path from 'node:path';
import { patchChanges } from './patch-diff.mjs';
const root=path.resolve('../auth_release/a_pi_site/dist');
let patch='*** Begin Patch\n';
function edit(name,transform){const file=path.join(root,name), old=fs.readFileSync(file,'utf8'), next=transform(old);if(old===next)throw new Error(name+' unchanged');patch+='*** Update File: '+file.replaceAll('\\','/')+'\n'+patchChanges(old,next);}
edit('index.html',old=>old.replace('<h2>进入本机工作区</h2>','<h2>登录 A-pi</h2>').replace('<p class="auth-muted">这是单机访客入口，不验证账号或密码。请勿输入真实凭据；远程访问必须由受保护的网关认证。</p>',`<p class="auth-muted">使用管理员创建的账号登录。研究预算、因子与纸面账户按用户独立保存。</p>
        <form id="authForm">
          <label class="auth-field" for="authUsername"><span>用户名</span><input id="authUsername" name="username" autocomplete="username" autocapitalize="none" spellcheck="false" required minlength="3" maxlength="32" pattern="[a-z0-9_-]{3,32}" aria-describedby="authUsernameHint" /></label>
          <p class="auth-hint" id="authUsernameHint">3–32 位小写字母、数字、下划线或连字符</p>
          <label class="auth-field" for="authPassword"><span>密码</span><input id="authPassword" name="password" type="password" autocomplete="current-password" required minlength="10" maxlength="1024" /></label>
          <p id="authError" class="auth-error" role="alert" aria-live="polite" hidden></p>`).replace('记住此设备的访问状态','保持登录 7 天').replace('<button class="primary-button full auth-submit" id="guestButton" type="button">进入研究工作区 <span>→</span></button>','<button class="primary-button full auth-submit" id="authSubmit" type="submit">登录</button></form>'));
edit('styles.css',old=>old+`\n/* Auth release: explicit labels and inline recovery states. */
.auth-field { display: flex; flex-direction: column; gap: 8px; margin-block-start: 20px; font-size: 16px; color: #243249; }
.auth-field input { width: 100%; min-height: 48px; padding: 12px; border: 1px solid #9ca9bd; border-radius: 12px; font: inherit; background: #fff; color: #172236; caret-color: #6045d7; }
.auth-field input:focus-visible { outline: 3px solid #7356ec; outline-offset: 3px; }
.auth-hint { margin-block: 8px 0; font-size: 14px; color: #4b596e; overflow-wrap: anywhere; }
.auth-error { color: #a12235; font-size: 16px; line-height: 1.5; overflow-wrap: anywhere; }
.auth-submit:disabled { opacity: .65; cursor: wait; }
.auth-card { overflow-wrap: anywhere; }
`);
edit('app-shell.mjs',old=>old.replace('const rawDisplayName = email.split("@")[0] || "用户";','const rawDisplayName = email || "用户";').replace(/  \/\*\* @param \{string\} email \*\/\r?\n  function persistSession\(email\) \{[\s\S]*?\r?\n  \}\r?\n/,'').replace('requiredElement("guestButton")?.focus();','requiredElement("authUsername")?.focus();').replace('    persistSession,\n',''));
edit('app.js',old=>{
 let next='import { createAuthClient } from "./auth-client.mjs";\n'+old;
 next=next.replace('const sessionKey = "a_pi_session";',`const sessionKey = "a_pi_session";
localStorage.removeItem(sessionKey);
sessionStorage.removeItem(sessionKey);
const authClient = createAuthClient({
  fetch: window.fetch.bind(window), origin: window.location.href,
  onExpired: () => {
    appShellRuntime.exitWorkspace();
    sessionStorage.setItem("a_pi_forced_login", "expired");
    window.location.reload();
  },
});
window.fetch = authClient.fetch;`);
 const start=next.indexOf('/** @param {string} email */\nfunction persistSession'),end=next.indexOf('const navItems =',start);
 if(start<0||end<0)throw new Error('auth region');
 next=next.slice(0,start)+`const authErrorMessages = {
  invalid_credentials: "用户名或密码错误，请重试。",
  unauthorized: "会话已过期，请重新登录。",
  too_many_attempts: "尝试次数过多，请 15 分钟后重试。",
  auth_not_configured: "尚未创建用户，请联系管理员在本机创建账号。",
  auth_network_error: "无法连接认证服务，请检查后端后重试。",
  auth_invalid_session: "认证服务响应异常，请检查后端版本。",
};
/** @param {string} code */
function showAuthError(code) {
  const message = authErrorMessages[/** @type {keyof typeof authErrorMessages} */ (code)] || authErrorMessages.auth_network_error;
  const error = requiredElement("authError");
  error.textContent = localeText(message);
  error.hidden = false;
}
async function exitWorkspace() {
  sessionStorage.setItem("a_pi_forced_login", "signed-out");
  appShellRuntime.exitWorkspace();
  try { await authClient.logout(); } catch { /* Offline still clears local identity. */ }
  window.location.reload();
}
requiredElement("authForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = requiredElement("authSubmit");
  if (button.disabled) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  requiredElement("authError").hidden = true;
  try {
    await authClient.login(requiredElement("authUsername").value, requiredElement("authPassword").value, requiredElement("rememberSession").checked);
    requiredElement("authPassword").value = "";
    sessionStorage.removeItem("a_pi_forced_login");
    window.location.reload();
  } catch (error) {
    showAuthError(error instanceof Error ? error.message : "auth_network_error");
  } finally {
    button.disabled = false;
    button.removeAttribute("aria-busy");
  }
});
requiredElement("logoutButton").addEventListener("click", exitWorkspace);
requiredElement("helpLink").addEventListener("click", (event) => {
  event.preventDefault();
  openUtilityDialog("访问帮助", '<p>账号由管理员通过本机用户管理工具创建。忘记密码时请联系管理员重置；不提供访客入口。</p>');
});
requiredElement("helpButton")?.addEventListener("click", openHelpDialog);
requiredElement("userMenuButton")?.addEventListener("click", () => {
  const session = authClient.current();
  if (!session) return;
  openUtilityDialog("当前会话", [
    '<div class="utility-grid">',
    dialogRow(localeText("用户名"), session.username),
    dialogRow(localeText("角色"), localeText(session.role === "admin" ? "管理员" : "研究员")),
    dialogRow(localeText("会话到期时间"), formatChinaTimestamp(session.expires_at)),
    '</div>',
    '<div class="utility-dialog-actions"><button class="secondary-button" type="button" data-close-utility>关闭</button><button class="primary-button" type="button" data-utility-logout>退出当前会话</button></div>',
  ].join(""));
});
\n`+next.slice(end);
 next=next.replace('window.addEventListener("load", () => {','window.addEventListener("load", async () => {');
 next=next.replace('  loadRuntimeConfig();',`  applyLocale(state.activeLocale);
  if (sessionStorage.getItem("a_pi_forced_login")) {
    if (sessionStorage.getItem("a_pi_forced_login") === "expired") showAuthError("unauthorized");
    return;
  }
  try {
    const session = await authClient.boot();
    if (!session) return;
    enterWorkspace(session.username, true);
  } catch { showAuthError("auth_network_error"); return; }
  loadRuntimeConfig();`);
 return next;
});
console.log(patch+'*** End Patch');
