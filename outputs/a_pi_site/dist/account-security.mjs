/** Account security uses only the authenticated fetch supplied by the app. */
/** @typedef {{jti:string,current:boolean,ip:string,user_agent:string,created_at:string,last_seen:string,expires_at:string}} DeviceSession */
/** @typedef {{username:string,role:string,disabled:boolean,created_at:string}} AccountUser */
/** @typedef {{sessions?:DeviceSession[],users?:AccountUser[],enabled?:boolean,secret?:string,otpauth_uri?:string,recovery_codes?:string[],code?:string,expires_at?:string}} Result */

export const securityDictionary = {
  "zh-CN": {
    title: "账户安全",
    close: "关闭",
    loading: "正在加载…",
    retry: "重试",
    sessions: "登录会话",
    current: "当前会话",
    ip: "IP 地址",
    device: "设备",
    created: "创建时间",
    seen: "最近活动",
    expires: "到期时间",
    revoke: "撤销会话",
    revokeAll: "撤销所有会话",
    password: "修改密码",
    oldPassword: "当前密码",
    newPassword: "新密码",
    confirmPassword: "确认新密码",
    otp: "验证码",
    optionalOtp: "验证码（可选）",
    save: "确认",
    cancel: "取消",
    pending: "正在提交…",
    twoFactor: "双重验证",
    enabled: "已启用",
    disabled: "未启用",
    setup: "设置双重验证",
    openTwoFactor: "打开双重验证",
    closeTwoFactor: "关闭双重验证",
    enable: "启用双重验证",
    disable: "停用双重验证",
    secret: "设置密钥",
    secretHint:
      "将此密钥手动添加到验证器，然后输入验证码。关闭窗口会清除此处的密钥。",
    recovery: "恢复码",
    recoveryHint: "恢复码仅显示一次。请保存到安全的位置，每个码只能使用一次。",
    users: "用户管理",
    username: "用户名",
    role: "角色",
    admin: "管理员",
    researcher: "研究员",
    status: "状态",
    active: "正常",
    suspended: "已停用",
    createUser: "创建用户",
    createInvite: "生成邀请码",
    inviteCode: "邀请码",
    inviteHint:
      "仅显示一次，请保存后发给受邀者。7 天内有效，每码只能注册一位研究员。",
    invite_limit: "有效且未使用的邀请码已达 20 个，请先撤销或等待过期。",
    initialPassword: "初始密码",
    changeRole: "修改角色",
    resetSessions: "重置用户会话",
    confirmHint: "确认执行此操作。撤销当前会话或所有会话后需要重新登录。",
    done: "操作已完成。",
    emptySessions: "没有可显示的会话。",
    emptyUsers: "没有可显示的用户。",
    mismatch: "两次输入的新密码不一致。",
    network: "请求失败，请检查连接后重试。",
    forbidden: "权限不足或当前密码/验证码错误，请检查后重试。",
    invalid: "输入无效，请检查后重试。",
    password_weak: "密码过于常见，请使用独特的长密码。",
    password_length: "密码长度须为 10–1024 个字符。",
    password_unchanged: "新密码不能与当前密码相同。",
    expired: "会话已过期，请重新登录。",
    loginOtp: "验证码或恢复码（可选）",
    loginOtpHint: "已启用双重验证时，请输入验证码或未使用的恢复码。",
    totp_required: "请输入验证码或未使用的恢复码。",
    totp_invalid: "验证码或恢复码无效，请重试。",
    logout: "退出登录",
    actions: "操作",
    unknown: "未知",
    unavailable: "服务响应异常，请重试。",
  },
  "en-US": {
    title: "Account security",
    close: "Close",
    loading: "Loading…",
    retry: "Retry",
    sessions: "Login sessions",
    current: "Current session",
    ip: "IP address",
    device: "Device",
    created: "Created",
    seen: "Last active",
    expires: "Expires",
    revoke: "Revoke session",
    revokeAll: "Revoke all sessions",
    password: "Change password",
    oldPassword: "Current password",
    newPassword: "New password",
    confirmPassword: "Confirm new password",
    otp: "Verification code",
    optionalOtp: "Verification code (optional)",
    save: "Confirm",
    cancel: "Cancel",
    pending: "Submitting…",
    twoFactor: "Two-factor authentication",
    enabled: "Enabled",
    disabled: "Disabled",
    setup: "Set up two-factor authentication",
    openTwoFactor: "Turn on two-factor authentication",
    closeTwoFactor: "Turn off two-factor authentication",
    enable: "Enable two-factor authentication",
    disable: "Disable two-factor authentication",
    secret: "Setup secret",
    secretHint:
      "Add this secret manually to your authenticator, then enter its code. Closing the window clears the secret shown here.",
    recovery: "Recovery codes",
    recoveryHint:
      "These codes are shown once. Save them somewhere secure. Each code can be used only once.",
    users: "User management",
    username: "Username",
    role: "Role",
    admin: "Administrator",
    researcher: "Researcher",
    status: "Status",
    active: "Active",
    suspended: "Disabled",
    createUser: "Create user",
    createInvite: "Generate invitation",
    inviteCode: "Invitation code",
    inviteHint:
      "Shown only once. Save it and share it with the recipient. Valid for 7 days; one researcher per code.",
    invite_limit:
      "You have 20 active unused invitations. Revoke one or wait for expiry.",
    initialPassword: "Initial password",
    changeRole: "Change role",
    resetSessions: "Reset user sessions",
    confirmHint:
      "Confirm this action. Revoking your current session or all sessions requires signing in again.",
    done: "Action completed.",
    emptySessions: "No sessions to display.",
    emptyUsers: "No users to display.",
    mismatch: "The new passwords do not match.",
    network: "Request failed. Check your connection and retry.",
    forbidden:
      "Permission denied or incorrect current password/code. Check and retry.",
    invalid: "Invalid input. Check the fields and retry.",
    password_weak:
      "This password is too common. Choose a unique long password.",
    password_length: "Use 10–1024 characters for your password.",
    password_unchanged:
      "Choose a password different from your current password.",
    expired: "Session expired. Please sign in again.",
    loginOtp: "Verification or recovery code (optional)",
    loginOtpHint:
      "If two-factor authentication is enabled, enter a verification code or an unused recovery code.",
    totp_required: "Enter a verification code or an unused recovery code.",
    totp_invalid: "Invalid verification or recovery code. Please retry.",
    logout: "Sign out",
    actions: "Actions",
    unknown: "Unknown",
    unavailable: "Unexpected service response. Please retry.",
  },
  "fr-FR": {
    title: "Sécurité du compte",
    close: "Fermer",
    loading: "Chargement…",
    retry: "Réessayer",
    sessions: "Sessions de connexion",
    current: "Session actuelle",
    ip: "Adresse IP",
    device: "Appareil",
    created: "Création",
    seen: "Dernière activité",
    expires: "Expiration",
    revoke: "Révoquer la session",
    revokeAll: "Révoquer toutes les sessions",
    password: "Modifier le mot de passe",
    oldPassword: "Mot de passe actuel",
    newPassword: "Nouveau mot de passe",
    confirmPassword: "Confirmer le nouveau mot de passe",
    otp: "Code de vérification",
    optionalOtp: "Code de vérification (facultatif)",
    save: "Confirmer",
    cancel: "Annuler",
    pending: "Envoi en cours…",
    twoFactor: "Authentification à deux facteurs",
    enabled: "Activée",
    disabled: "Désactivée",
    setup: "Configurer l’authentification à deux facteurs",
    openTwoFactor: "Activer l’authentification à deux facteurs",
    closeTwoFactor: "Désactiver l’authentification à deux facteurs",
    enable: "Activer l’authentification à deux facteurs",
    disable: "Désactiver l’authentification à deux facteurs",
    secret: "Clé de configuration",
    secretHint:
      "Ajoutez manuellement cette clé à votre application d’authentification, puis saisissez son code. Fermer la fenêtre efface la clé affichée ici.",
    recovery: "Codes de récupération",
    recoveryHint:
      "Ces codes ne sont affichés qu’une fois. Conservez-les en lieu sûr. Chaque code ne peut être utilisé qu’une fois.",
    users: "Gestion des utilisateurs",
    username: "Nom d’utilisateur",
    role: "Rôle",
    admin: "Administrateur",
    researcher: "Chercheur",
    status: "État",
    active: "Actif",
    suspended: "Désactivé",
    createUser: "Créer un utilisateur",
    createInvite: "Générer une invitation",
    inviteCode: "Code d’invitation",
    inviteHint:
      "Affiché une seule fois. Enregistrez-le et transmettez-le au destinataire. Valable 7 jours pour un seul chercheur.",
    invite_limit:
      "Vous avez 20 invitations actives inutilisées. Révoquez-en une ou attendez son expiration.",
    initialPassword: "Mot de passe initial",
    changeRole: "Modifier le rôle",
    resetSessions: "Réinitialiser les sessions",
    confirmHint:
      "Confirmez cette action. Révoquer la session actuelle ou toutes les sessions nécessite une nouvelle connexion.",
    done: "Action terminée.",
    emptySessions: "Aucune session à afficher.",
    emptyUsers: "Aucun utilisateur à afficher.",
    mismatch: "Les nouveaux mots de passe ne correspondent pas.",
    network: "Échec de la requête. Vérifiez la connexion et réessayez.",
    forbidden:
      "Accès refusé ou mot de passe actuel/code incorrect. Vérifiez et réessayez.",
    invalid: "Saisie invalide. Vérifiez les champs et réessayez.",
    password_weak:
      "Ce mot de passe est trop courant. Choisissez un mot de passe long et unique.",
    password_length: "Utilisez un mot de passe de 10 à 1024 caractères.",
    password_unchanged:
      "Choisissez un mot de passe différent du mot de passe actuel.",
    expired: "Session expirée. Veuillez vous reconnecter.",
    loginOtp: "Code de vérification ou de récupération (facultatif)",
    loginOtpHint:
      "Si l’authentification à deux facteurs est activée, saisissez un code de vérification ou de récupération inutilisé.",
    totp_required:
      "Saisissez un code de vérification ou de récupération inutilisé.",
    totp_invalid:
      "Code de vérification ou de récupération invalide. Réessayez.",
    logout: "Se déconnecter",
    actions: "Actions",
    unknown: "Inconnu",
    unavailable: "Réponse du service inattendue. Réessayez.",
  },
  "ja-JP": {
    title: "アカウントのセキュリティ",
    close: "閉じる",
    loading: "読み込み中…",
    retry: "再試行",
    sessions: "ログインセッション",
    current: "現在のセッション",
    ip: "IP アドレス",
    device: "端末",
    created: "作成日時",
    seen: "最終利用",
    expires: "有効期限",
    revoke: "セッションを取り消す",
    revokeAll: "すべてのセッションを取り消す",
    password: "パスワードを変更",
    oldPassword: "現在のパスワード",
    newPassword: "新しいパスワード",
    confirmPassword: "新しいパスワードを再入力",
    otp: "認証コード",
    optionalOtp: "認証コード（任意）",
    save: "確認",
    cancel: "キャンセル",
    pending: "送信中…",
    twoFactor: "二要素認証",
    enabled: "有効",
    disabled: "無効",
    setup: "二要素認証を設定",
    openTwoFactor: "二要素認証を有効にする",
    closeTwoFactor: "二要素認証を無効にする",
    enable: "二要素認証を有効化",
    disable: "二要素認証を無効化",
    secret: "設定用シークレット",
    secretHint:
      "このシークレットを認証アプリに手動で追加し、認証コードを入力してください。ウィンドウを閉じると表示中のシークレットは消去されます。",
    recovery: "リカバリーコード",
    recoveryHint:
      "コードは一度だけ表示されます。安全な場所に保存してください。各コードは一度だけ使用できます。",
    users: "ユーザー管理",
    username: "ユーザー名",
    role: "権限",
    admin: "管理者",
    researcher: "研究者",
    status: "状態",
    active: "有効",
    suspended: "無効",
    createUser: "ユーザーを作成",
    createInvite: "招待コードを生成",
    inviteCode: "招待コード",
    inviteHint:
      "一度だけ表示されます。保存して招待する方に渡してください。有効期間は7日間で、研究員1人の登録に使用できます。",
    invite_limit:
      "有効な未使用の招待コードが20件あります。取り消すか有効期限をお待ちください。",
    initialPassword: "初期パスワード",
    changeRole: "権限を変更",
    resetSessions: "ユーザーのセッションをリセット",
    confirmHint:
      "この操作を確認してください。現在またはすべてのセッションを取り消すと再ログインが必要です。",
    done: "操作が完了しました。",
    emptySessions: "表示するセッションがありません。",
    emptyUsers: "表示するユーザーがありません。",
    mismatch: "新しいパスワードが一致しません。",
    network: "リクエストに失敗しました。接続を確認して再試行してください。",
    forbidden:
      "権限がないか、現在のパスワードまたはコードが正しくありません。確認して再試行してください。",
    invalid: "入力が無効です。入力内容を確認してください。",
    password_weak:
      "一般的すぎるパスワードです。長く固有のものを設定してください。",
    password_length: "パスワードは10〜1024文字で設定してください。",
    password_unchanged: "現在のものとは異なるパスワードを設定してください。",
    expired: "セッションが切れました。再ログインしてください。",
    loginOtp: "認証コードまたはリカバリーコード（任意）",
    loginOtpHint:
      "二要素認証が有効な場合は認証コードか未使用のリカバリーコードを入力してください。",
    totp_required: "認証コードか未使用のリカバリーコードを入力してください。",
    totp_invalid:
      "認証コードまたはリカバリーコードが無効です。再試行してください。",
    logout: "ログアウト",
    actions: "操作",
    unknown: "不明",
    unavailable: "サービスの応答が不正です。再試行してください。",
  },
};
/** @typedef {keyof typeof securityDictionary['en-US']} TextKey */
/** @param {string} locale @param {TextKey} key */
export function securityText(locale, key) {
  return (securityDictionary[
    /** @type {keyof typeof securityDictionary} */ (locale)
  ] || securityDictionary["en-US"])[key];
}

/** @param {typeof fetch} fetcher */
export function createSecurityApi(fetcher) {
  /** @param {string} path @param {Record<string,unknown>|undefined} [body] @returns {Promise<Result>} */
  async function request(path, body) {
    const response = await fetcher(path, {
      method: body === undefined ? "GET" : "POST",
      credentials: "same-origin",
      cache: "no-store",
      ...(body === undefined
        ? {}
        : {
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body),
          }),
    });
    if (!response.ok) {
      const result = await response.json().catch(() => ({}));
      throw new Error(
        [
          "totp_required",
          "totp_invalid",
          "password_weak",
          "password_length",
          "password_unchanged",
          "invite_limit",
        ].includes(result.error)
          ? result.error
          : response.status === 403
            ? "forbidden"
            : response.status === 401
              ? "expired"
              : response.status === 400 ||
                  response.status === 409 ||
                  response.status === 422
                ? "invalid"
                : "network",
      );
    }
    return response.status === 204 ? {} : response.json();
  }
  return { request };
}

/** @param {{fetch:typeof fetch,session:()=>import('./auth-client.mjs').Session|null,locale:()=>string,logout:()=>Promise<void>,forget:()=>void}} options */
export function createAccountSecurity(options) {
  const api = createSecurityApi(options.fetch);
  /** @type {HTMLDialogElement|null} */
  let dialog = null;
  let epoch = 0;
  /** @param {TextKey} key */
  const t = (key) => securityText(options.locale(), key);
  /** @template {keyof HTMLElementTagNameMap} K @param {K} tag @param {string} [text] */
  function el(tag, text = "") {
    const node = document.createElement(tag);
    node.textContent = text;
    return node;
  }
  /** @param {TextKey} key @param {()=>void} action */
  function button(key, action) {
    const node = el("button", t(key));
    node.type = "button";
    node.className = "secondary-button";
    node.addEventListener("click", action);
    return node;
  }
  /** @param {unknown} error */
  function errorText(error) {
    const code = error instanceof Error ? error.message : "network";
    return t(
      /** @type {TextKey} */ (
        [
          "forbidden",
          "expired",
          "invalid",
          "totp_required",
          "totp_invalid",
          "password_weak",
          "password_length",
          "password_unchanged",
          "invite_limit",
          "unavailable",
          "mismatch",
        ].includes(code)
          ? code
          : "network"
      ),
    );
  }
  /** @param {string} value */
  function date(value) {
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime())
      ? t("unknown")
      : new Intl.DateTimeFormat(options.locale(), {
          dateStyle: "medium",
          timeStyle: "short",
          timeZone: "Asia/Shanghai",
        }).format(parsed);
  }
  /** @param {TextKey} title @param {{key:string,label:TextKey,type?:string,required?:boolean}[]} fields @param {(body:Record<string,string>)=>Promise<void>} submit */
  function action(title, fields, submit) {
    const owner = dialog;
    if (!owner) return;
    const modal = el("dialog");
    modal.className = "account-security account-security-action";
    modal.lang = options.locale();
    const heading = el("h2", t(title));
    heading.id = "security-action-title";
    modal.setAttribute("aria-labelledby", heading.id);
    const form = el("form");
    const error = el("p");
    error.className = "security-error";
    error.setAttribute("role", "alert");
    error.setAttribute("aria-live", "polite");
    error.id = "security-action-error";
    for (const field of fields) {
      const label = el("label", t(field.label));
      const input = field.type === "role" ? el("select") : el("input");
      input.name = field.key;
      input.id = `security-${field.key}`;
      label.htmlFor = input.id;
      input.required = field.required !== false;
      input.setAttribute("aria-describedby", error.id);
      if (input instanceof HTMLInputElement) {
        input.type = field.type || "text";
        input.autocomplete =
          field.key === "otp"
            ? "one-time-code"
            : field.type === "password"
              ? field.key === "current_password" || field.key === "old_password"
                ? "current-password"
                : "new-password"
              : "username";
        if (field.type === "password") {
          input.minLength = 10;
          input.maxLength = 1024;
        }
        if (field.key === "username") {
          input.pattern = "[a-z0-9_\\-]{3,32}";
          input.minLength = 3;
          input.maxLength = 32;
        }
        input.spellcheck = false;
        input.autocapitalize = "none";
      } else {
        for (const role of ["researcher", "admin"]) {
          const option = el(
            "option",
            t(role === "admin" ? "admin" : "researcher"),
          );
          option.value = role;
          input.append(option);
        }
      }
      form.append(label, input);
    }
    const confirm = el("button", t("save"));
    confirm.type = "submit";
    confirm.className = "primary-button";
    const cancel = button("cancel", () => modal.close());
    const actions = el("div");
    actions.className = "security-actions";
    actions.append(cancel, confirm);
    form.append(error, actions);
    modal.append(heading, el("p", t("confirmHint")), form);
    document.body.append(modal);
    let pending = false;
    const started = epoch;
    const opener = document.activeElement;
    modal.addEventListener("close", () => {
      form.reset();
      modal.remove();
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
      else if (owner.isConnected) owner.focus();
    });
    owner.addEventListener("close", () => modal.close(), { once: true });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (pending) return;
      /** @type {Record<string,string>} */
      const values = {};
      for (const [key, value] of new FormData(form).entries())
        values[key] = String(value);
      pending = true;
      error.textContent = "";
      form.setAttribute("aria-busy", "true");
      const controls = [...form.querySelectorAll("input,select,button")].filter(
        (node) =>
          node instanceof HTMLInputElement ||
          node instanceof HTMLSelectElement ||
          node instanceof HTMLButtonElement,
      );
      controls.forEach((node) => {
        node.disabled = true;
      });
      confirm.textContent = t("pending");
      try {
        await submit(values);
        if (started === epoch && modal.isConnected) modal.close();
      } catch (err) {
        if (started === epoch && modal.isConnected)
          error.textContent = errorText(err);
      } finally {
        pending = false;
        form.removeAttribute("aria-busy");
        controls.forEach((node) => {
          node.disabled = false;
        });
        confirm.textContent = t("save");
        for (const key of Object.keys(values)) values[key] = "";
      }
    });
    modal.showModal();
  }
  /** @param {HTMLElement} host @param {string} secret @param {string[]} codes @param {boolean} [invitation] */
  function showSecrets(host, secret, codes, invitation = false) {
    host.replaceChildren();
    host.append(
      el("h3", t(invitation ? "inviteCode" : secret ? "secret" : "recovery")),
      el(
        "p",
        t(invitation ? "inviteHint" : secret ? "secretHint" : "recoveryHint"),
      ),
    );
    const output = el("pre", secret || codes.join("\n"));
    output.tabIndex = 0;
    host.append(output);
    output.focus();
  }
  /** @param {HTMLElement} host */
  async function load(host) {
    const started = epoch;
    host.replaceChildren(el("p", t("loading")));
    host.setAttribute("aria-busy", "true");
    const session = options.session();
    if (!session) return;
    const responses = await Promise.allSettled([
      api.request("/api/auth/sessions"),
      api.request("/api/auth/2fa/status"),
      ...(session.role === "admin" ? [api.request("/api/admin/users")] : []),
    ]);
    if (started !== epoch || !host.isConnected) return;
    host.replaceChildren();
    host.removeAttribute("aria-busy");
    /** @param {TextKey} title */
    function section(title) {
      const node = el("section");
      node.append(el("h3", t(title)));
      host.append(node);
      return node;
    }
    /** @param {HTMLElement} node @param {unknown} error */
    function failed(node, error) {
      const message = el("p", errorText(error));
      message.className = "security-error";
      message.setAttribute("role", "alert");
      node.append(
        message,
        button("retry", () => void load(host)),
      );
    }
    const sessions = section("sessions");
    const devices = responses[0];
    if (devices.status === "rejected") failed(sessions, devices.reason);
    else if (!Array.isArray(devices.value.sessions))
      failed(sessions, new Error("unavailable"));
    else {
      if (!devices.value.sessions.length)
        sessions.append(el("p", t("emptySessions")));
      for (const item of devices.value.sessions) {
        const row = el("article");
        row.className = "security-session";
        row.append(
          el(
            "h4",
            item.current ? t("current") : item.user_agent || t("unknown"),
          ),
        );
        const details = el("dl");
        for (const [key, value] of /** @type {[TextKey,string][]} */ ([
          ["device", item.user_agent],
          ["ip", item.ip],
          ["created", date(item.created_at)],
          ["seen", date(item.last_seen)],
          ["expires", date(item.expires_at)],
        ])) {
          details.append(el("dt", t(key)), el("dd", value || t("unknown")));
        }
        row.append(
          details,
          button("revoke", () =>
            action("revoke", [], async () => {
              await api.request("/api/auth/revoke-session", { jti: item.jti });
              if (item.current) await options.logout();
              else if (started === epoch) await load(host);
            }),
          ),
        );
        sessions.append(row);
      }
      sessions.append(
        button("revokeAll", () =>
          action("revokeAll", [], async () => {
            await api.request("/api/auth/revoke-all", {});
            await options.logout();
          }),
        ),
      );
    }
    const password = section("password");
    password.append(
      button("password", () =>
        action(
          "password",
          [
            { key: "new_password", label: "newPassword", type: "password" },
            {
              key: "confirm_password",
              label: "confirmPassword",
              type: "password",
            },
          ],
          async (values) => {
            if (values.new_password !== values.confirm_password)
              throw new Error("mismatch");
            const { confirm_password, ...body } = values;
            await api.request("/api/auth/change-password", body);
            await options.logout();
          },
        ),
      ),
    );
    const twoFactor = section("twoFactor");
    const factor = responses[1];
    const secrets = el("div");
    secrets.className = "security-secrets";
    /** @type {{key:string,label:TextKey,type?:string,required?:boolean}[]} */
    const credentials = [];
    /** @type {HTMLParagraphElement|null} */
    let factorStatus = null;
    if (factor.status === "rejected") failed(twoFactor, factor.reason);
    else if (typeof factor.value.enabled !== "boolean")
      failed(twoFactor, new Error("unavailable"));
    else {
      const factorActions = el("div");
      factorActions.className =
        "security-inline-actions security-factor-actions";
      if (factor.value.enabled) {
        factorStatus = el("p", t("enabled"));
        factorStatus.className = "security-factor-status";
        factorActions.append(factorStatus);
      }
      twoFactor.append(factorActions);
      const openButton = button("openTwoFactor", () =>
        action("setup", credentials, async (body) => {
          const result = await api.request("/api/auth/2fa/setup", body);
          if (!result.secret || !result.otpauth_uri)
            throw new Error("unavailable");
          if (started !== epoch) return;
          showSecrets(secrets, result.secret, []);
          secrets.append(
            button("enable", () =>
              action(
                "enable",
                [{ key: "otp", label: "otp" }],
                async (values) => {
                  const enabled = await api.request(
                    "/api/auth/2fa/enable",
                    values,
                  );
                  if (
                    !Array.isArray(enabled.recovery_codes) ||
                    !enabled.recovery_codes.length
                  )
                    throw new Error("unavailable");
                  if (started !== epoch) return;
                  openButton.disabled = true;
                  closeButton.disabled = false;
                  factorStatus = el("p", t("enabled"));
                  factorStatus.className = "security-factor-status";
                  factorActions.prepend(factorStatus);
                  showSecrets(host, "", enabled.recovery_codes);
                },
              ),
            ),
          );
        }),
      );
      openButton.disabled = factor.value.enabled;
      const closeButton = button("closeTwoFactor", () =>
        action("disable", [], async (body) => {
          await api.request("/api/auth/2fa/disable", body);
          openButton.disabled = false;
          closeButton.disabled = true;
          factorStatus?.remove();
          factorStatus = null;
        }),
      );
      closeButton.disabled = !factor.value.enabled;
      factorActions.append(openButton, closeButton);
    }
    twoFactor.append(secrets);
    if (session.role === "admin") {
      const users = section("users");
      const invitationSecrets = el("div");
      const userActions = el("div");
      userActions.className = "security-inline-actions security-user-actions";
      users.append(userActions, invitationSecrets);
      userActions.append(
        button("createInvite", () =>
          action("createInvite", credentials, async (body) => {
            const invitation = await api.request("/api/admin/invites", body);
            if (
              typeof invitation.code !== "string" ||
              typeof invitation.expires_at !== "string"
            )
              throw new Error("unavailable");
            if (started !== epoch) return;
            showSecrets(invitationSecrets, invitation.code, [], true);
            invitationSecrets.append(el("p", date(invitation.expires_at)));
          }),
        ),
      );
      const result = responses[2];
      userActions.append(
        button("createUser", () =>
          action(
            "createUser",
            [
              { key: "username", label: "username" },
              { key: "password", label: "initialPassword", type: "password" },
              { key: "role", label: "role", type: "role" },
              ...credentials,
            ],
            async (body) => {
              await api.request("/api/admin/users", body);
              if (started === epoch) await load(host);
            },
          ),
        ),
      );
      if (result.status === "rejected") failed(users, result.reason);
      else if (!Array.isArray(result.value.users))
        failed(users, new Error("unavailable"));
      else {
        const scroll = el("div");
        scroll.className = "security-table-scroll";
        scroll.tabIndex = 0;
        scroll.setAttribute("role", "region");
        scroll.setAttribute("aria-label", t("users"));
        const table = el("table");
        const caption = el("caption", t("users"));
        table.append(caption);
        const head = el("thead");
        const tr = el("tr");
        for (const key of /** @type {TextKey[]} */ ([
          "username",
          "role",
          "status",
          "created",
          "actions",
        ])) {
          const th = el("th", t(key));
          th.scope = "col";
          tr.append(th);
        }
        head.append(tr);
        table.append(head);
        const tbody = el("tbody");
        for (const user of result.value.users) {
          const row = el("tr");
          for (const text of [
            user.username,
            user.role === "admin" ? t("admin") : t("researcher"),
            t(user.disabled ? "suspended" : "active"),
            date(user.created_at),
          ])
            row.append(el("td", text));
          const actions = el("td");
          for (const name of ["disable", "enable", "role", "reset-sessions"]) {
            if (
              (name === "disable" && user.disabled) ||
              (name === "enable" && !user.disabled)
            )
              continue;
            const key =
              name === "role"
                ? "changeRole"
                : name === "reset-sessions"
                  ? "resetSessions"
                  : name;
            actions.append(
              button(/** @type {TextKey} */ (key), () =>
                action(
                  /** @type {TextKey} */ (key),
                  [
                    ...(name === "role"
                      ? [
                          {
                            key: "role",
                            label: /** @type {TextKey} */ ("role"),
                            type: "role",
                          },
                        ]
                      : []),
                    ...credentials,
                  ],
                  async (body) => {
                    await api.request("/api/admin/users/action", {
                      ...body,
                      username: user.username,
                      action: name,
                    });
                    if (started === epoch) await load(host);
                  },
                ),
              ),
            );
          }
          row.append(actions);
          tbody.append(row);
        }
        table.append(tbody);
        scroll.append(table);
        users.append(scroll);
        if (!result.value.users.length) users.append(el("p", t("emptyUsers")));
      }
    }
  }
  function open() {
    if (dialog?.open || !options.session()) return;
    epoch += 1;
    const opener = document.activeElement;
    dialog = el("dialog");
    dialog.className = "account-security";
    dialog.lang = options.locale();
    dialog.tabIndex = -1;
    const heading = el("h2", t("title"));
    heading.id = "security-title";
    dialog.setAttribute("aria-labelledby", heading.id);
    const header = el("header");
    header.append(
      heading,
      button("close", () => dialog?.close()),
    );
    const identity = el("p", options.session()?.username);
    const host = el("div");
    host.setAttribute("aria-live", "polite");
    dialog.append(
      header,
      identity,
      host,
      button("logout", () => action("logout", [], options.logout)),
    );
    const owner = dialog;
    owner.addEventListener("close", () => {
      epoch += 1;
      owner.replaceChildren();
      owner.remove();
      dialog = null;
      if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
    });
    document.body.append(owner);
    owner.showModal();
    void load(host);
  }
  return { open };
}
