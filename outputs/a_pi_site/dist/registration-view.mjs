export const registrationDictionary = {
  "zh-CN": {
    title: "邀请码注册",
    description: "持有管理员的邀请码，可注册独立的研究员账号。",
    loginDescription:
      "使用管理员创建或邀请码注册的账号登录。研究预算、因子与纸面账户按用户独立保存。",
    username: "用户名",
    usernameHint: "3–32 位小写字母、数字、下划线或连字符",
    password: "密码",
    passwordHint: "10–1024 个字符，请使用独特的长密码。",
    invite: "邀请码",
    inviteHint: "由管理员提供，7 天内有效，每码仅可使用一次。",
    submit: "注册研究员账号",
    toggle: "使用邀请码注册",
    back: "返回登录",
    loginTitle: "登录 A-pi",
    pending: "正在注册…",
    success: "注册成功，请使用你的用户名和密码登录。",
    user_exists: "用户名已存在，请换一个用户名。",
    invite_invalid: "邀请码无效或不可使用，请联系管理员。",
    too_many_attempts: "尝试次数过多，请 15 分钟后重试。",
    invalid_request: "输入无效，请检查用户名、密码和邀请码。",
    password_weak: "密码过于常见，请使用独特的长密码。",
    password_length: "密码长度须为 10–1024 个字符。",
    auth_network_error: "无法连接认证服务，请稍后重试。",
  },
  "en-US": {
    title: "Invitation registration",
    description:
      "Use an administrator invitation to create your own researcher account.",
    loginDescription:
      "Sign in with an administrator-created or invitation-registered account. Research and paper accounts are private to each user.",
    username: "Username",
    usernameHint: "3–32 lowercase letters, digits, underscores or hyphens",
    password: "Password",
    passwordHint: "10–1024 characters. Choose a unique long password.",
    invite: "Invitation code",
    inviteHint: "Provided by an administrator. Valid for 7 days; single use.",
    submit: "Create researcher account",
    toggle: "Register with an invitation",
    back: "Back to sign in",
    loginTitle: "Sign in to A-pi",
    pending: "Registering…",
    success: "Account created. Sign in with your username and password.",
    user_exists: "This username is taken. Choose another.",
    invite_invalid:
      "This invitation cannot be used. Contact the administrator.",
    too_many_attempts: "Too many attempts. Retry in 15 minutes.",
    invalid_request: "Check your username, password and invitation code.",
    password_weak:
      "This password is too common. Choose a unique long password.",
    password_length: "Use 10–1024 characters for your password.",
    auth_network_error:
      "Cannot reach authentication service. Please retry later.",
  },
  "fr-FR": {
    title: "Inscription sur invitation",
    description:
      "Utilisez une invitation de l’administrateur pour créer votre compte de chercheur.",
    loginDescription:
      "Connectez-vous avec un compte créé par l’administrateur ou sur invitation. Les recherches et comptes simulés sont propres à chaque utilisateur.",
    username: "Nom d’utilisateur",
    usernameHint: "3 à 32 lettres minuscules, chiffres, tirets bas ou tirets",
    password: "Mot de passe",
    passwordHint:
      "10 à 1024 caractères. Choisissez un mot de passe long et unique.",
    invite: "Code d’invitation",
    inviteHint:
      "Fourni par l’administrateur. Valable 7 jours et utilisable une seule fois.",
    submit: "Créer un compte de chercheur",
    toggle: "S’inscrire sur invitation",
    back: "Retour à la connexion",
    loginTitle: "Connexion à A-pi",
    pending: "Inscription…",
    success:
      "Compte créé. Connectez-vous avec votre nom d’utilisateur et mot de passe.",
    user_exists: "Ce nom est déjà utilisé. Choisissez-en un autre.",
    invite_invalid:
      "Cette invitation ne peut pas être utilisée. Contactez l’administrateur.",
    too_many_attempts: "Trop de tentatives. Réessayez dans 15 minutes.",
    invalid_request: "Vérifiez votre nom, mot de passe et code d’invitation.",
    password_weak:
      "Ce mot de passe est trop courant. Choisissez un mot de passe long et unique.",
    password_length: "Utilisez un mot de passe de 10 à 1024 caractères.",
    auth_network_error:
      "Service d’authentification inaccessible. Réessayez plus tard.",
  },
  "ja-JP": {
    title: "招待コードで登録",
    description:
      "管理者の招待コードで、独立した研究員アカウントを作成できます。",
    loginDescription:
      "管理者が作成した、または招待コードで登録したアカウントでログインしてください。研究とペーパー口座はユーザーごとに独立しています。",
    username: "ユーザー名",
    usernameHint: "3〜32文字の小文字英字、数字、アンダースコア、ハイフン",
    password: "パスワード",
    passwordHint: "10〜1024文字。長く固有のパスワードを設定してください。",
    invite: "招待コード",
    inviteHint:
      "管理者から提供されます。有効期間は7日間で、一度だけ使用できます。",
    submit: "研究員アカウントを登録",
    toggle: "招待コードで登録",
    back: "ログインに戻る",
    loginTitle: "A-pi にログイン",
    pending: "登録中…",
    success: "登録できました。ユーザー名とパスワードでログインしてください。",
    user_exists:
      "このユーザー名は使用されています。別の名前を設定してください。",
    invite_invalid:
      "この招待コードは使用できません。管理者にお問い合わせください。",
    too_many_attempts: "試行回数が多すぎます。15分後に再試行してください。",
    invalid_request: "ユーザー名、パスワード、招待コードを確認してください。",
    password_weak:
      "一般的すぎるパスワードです。長く固有のものを設定してください。",
    password_length: "パスワードは10〜1024文字で設定してください。",
    auth_network_error:
      "認証サービスに接続できません。後で再試行してください。",
  },
};
/** @param {string} locale @param {string} key */
export function registrationText(locale, key) {
  const dictionary =
    registrationDictionary[
      /** @type {keyof typeof registrationDictionary} */ (locale)
    ] || registrationDictionary["en-US"];
  return (
    dictionary[/** @type {keyof typeof dictionary} */ (key)] ||
    dictionary.auth_network_error
  );
}

/** @param {{client:ReturnType<import('./auth-client.mjs').createAuthClient>,locale:()=>string}} options */
export function createRegistrationView(options) {
  const form = document.getElementById("registerForm");
  const login = document.getElementById("authForm");
  const toggle = document.getElementById("registerToggle");
  const submit = document.getElementById("registerSubmit");
  const error = document.getElementById("registerError");
  const success = document.getElementById("registerSuccess");
  const username = document.getElementById("registerUsername");
  const password = document.getElementById("registerPassword");
  const invite = document.getElementById("registerInvite");
  if (
    !(form instanceof HTMLFormElement) ||
    !(submit instanceof HTMLButtonElement) ||
    !(username instanceof HTMLInputElement) ||
    !(password instanceof HTMLInputElement) ||
    !(invite instanceof HTMLInputElement) ||
    !login ||
    !toggle ||
    !error ||
    !success
  )
    throw new Error("registration_dom_missing");
  const nodes = {
    form,
    login,
    toggle,
    submit,
    error,
    success,
    username,
    password,
    invite,
  };
  let expanded = false;
  let pending = false;
  let errorCode = "";
  const t = /** @param {string} key */ (key) =>
    registrationText(options.locale(), key);
  function refresh() {
    for (const node of nodes.form.querySelectorAll("[data-registration-text]"))
      node.textContent = t(node.getAttribute("data-registration-text") || "");
    nodes.toggle.hidden = !!options.client.current()?.must_change_password;
    nodes.toggle.textContent = t(expanded ? "back" : "toggle");
    nodes.submit.textContent = t(pending ? "pending" : "submit");
    if (errorCode) nodes.error.textContent = t(errorCode);
    if (!nodes.success.hidden) nodes.success.textContent = t("success");
    if (!options.client.current()?.must_change_password) {
      for (const [id, key] of [
        ["authTitle", expanded ? "title" : "loginTitle"],
        ["authDescription", expanded ? "description" : "loginDescription"],
      ]) {
        const node = document.getElementById(id);
        if (node) {
          node.setAttribute("data-locale-skip", "");
          node.textContent = t(key);
        }
      }
    }
  }
  /** @param {boolean} value */
  function setExpanded(value) {
    expanded = value;
    nodes.form.hidden = !value;
    nodes.login.hidden = value;
    nodes.toggle.setAttribute("aria-expanded", String(value));
    nodes.password.value = "";
    nodes.invite.value = "";
    nodes.error.hidden = true;
    errorCode = "";
    refresh();
    document
      .getElementById(value ? "registerUsername" : "authUsername")
      ?.focus();
  }
  nodes.toggle.addEventListener("click", () => {
    if (pending || options.client.current()) return;
    nodes.success.hidden = true;
    setExpanded(!expanded);
  });
  nodes.form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (pending) return;
    pending = true;
    nodes.submit.disabled = true;
    nodes.toggle.setAttribute("disabled", "");
    nodes.form.setAttribute("aria-busy", "true");
    nodes.error.hidden = true;
    refresh();
    try {
      const registered = await options.client.register(
        nodes.username.value,
        nodes.password.value,
        nodes.invite.value.trim(),
      );
      nodes.form.reset();
      setExpanded(false);
      const loginUser = document.getElementById("authUsername");
      if (loginUser instanceof HTMLInputElement) loginUser.value = registered;
      nodes.success.hidden = false;
      refresh();
      document.getElementById("authPassword")?.focus();
    } catch (failure) {
      errorCode =
        failure instanceof Error ? failure.message : "auth_network_error";
      nodes.error.hidden = false;
      refresh();
    } finally {
      pending = false;
      nodes.submit.disabled = false;
      nodes.toggle.removeAttribute("disabled");
      nodes.form.removeAttribute("aria-busy");
      refresh();
    }
  });
  refresh();
  return { refresh };
}
