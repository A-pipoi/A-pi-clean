/** First-login password replacement reuses the existing login surface. */
const copy = {
  "zh-CN": [
    "设置个人密码",
    "初始密码由管理员设置。更换为只有你知道的密码后，才能进入工作台。",
    "新密码",
    "确认新密码",
    "更换密码并重新登录",
    "退出",
    "两次新密码不一致。",
    "新密码不能与初始密码相同。",
    "密码过于常见，请使用独特的长密码。",
    "密码长度须为 10–1024 个字符。",
    "改密失败，请检查当前密码及验证码后重试。",
  ],
  "en-US": [
    "Set your own password",
    "Replace the administrator-issued password before entering the workbench.",
    "New password",
    "Confirm new password",
    "Change password and sign in again",
    "Sign out",
    "New passwords do not match.",
    "Choose a password different from the initial password.",
    "This password is too common. Choose a unique long password.",
    "Use 10–1024 characters.",
    "Could not change password. Check your current password and verification code.",
  ],
  "fr-FR": [
    "Définissez votre mot de passe",
    "Remplacez le mot de passe fourni par l’administrateur avant d’accéder à l’espace de travail.",
    "Nouveau mot de passe",
    "Confirmer le nouveau mot de passe",
    "Modifier puis se reconnecter",
    "Se déconnecter",
    "Les nouveaux mots de passe diffèrent.",
    "Choisissez un mot de passe différent du mot de passe initial.",
    "Ce mot de passe est trop courant. Choisissez un mot de passe long et unique.",
    "Utilisez 10 à 1024 caractères.",
    "Échec. Vérifiez votre mot de passe actuel et le code de vérification.",
  ],
  "ja-JP": [
    "個人用パスワードを設定",
    "管理者が設定した初期パスワードを変更してから、ワークスペースに入ってください。",
    "新しいパスワード",
    "新しいパスワードの確認",
    "変更して再ログイン",
    "ログアウト",
    "新しいパスワードが一致しません。",
    "初期パスワードとは異なるパスワードを設定してください。",
    "一般的すぎるパスワードです。長く固有のものを設定してください。",
    "10〜1024文字で設定してください。",
    "変更できませんでした。現在のパスワードと認証コードを確認してください。",
  ],
};

/** @param {{fetch:typeof fetch, locale:()=>string, logout:()=>Promise<void>}} options */
export function createInitialPassword(options) {
  /** @type {HTMLInputElement[]} */
  const inputs = [];
  /** @param {string} username */
  function activate(username) {
    const text =
      copy[/** @type {keyof typeof copy} */ (options.locale())] ||
      copy["zh-CN"];
    const form = document.getElementById("authForm");
    const user = document.getElementById("authUsername");
    if (!form || !(user instanceof HTMLInputElement)) return;
    user.value = username;
    user.readOnly = true;
    const initialLabel =
      {
        "zh-CN": "初始密码",
        "en-US": "Initial password",
        "fr-FR": "Mot de passe initial",
        "ja-JP": "初期パスワード",
      }[options.locale()] || "Initial password";
    const oldLabel = document
      .getElementById("authPassword")
      ?.parentElement?.querySelector("span");
    if (oldLabel) {
      oldLabel.textContent = initialLabel;
      oldLabel.setAttribute("data-locale-skip", "");
    }
    const card = form.parentElement;
    const heading = card?.querySelector("h2");
    const hint = card?.querySelector(".auth-muted");
    if (heading) {
      heading.textContent = text[0];
      heading.setAttribute("data-locale-skip", "");
    }
    if (hint) {
      hint.textContent = text[1];
      hint.setAttribute("data-locale-skip", "");
    }
    if (!inputs.length) {
      for (const index of [2, 3]) {
        const label = document.createElement("label");
        label.className = "auth-field";
        label.setAttribute("data-locale-skip", "");
        const span = document.createElement("span");
        span.textContent = text[index];
        const input = document.createElement("input");
        input.id = `initial-password-${index}`;
        label.htmlFor = input.id;
        input.type = "password";
        input.autocomplete = "new-password";
        input.required = true;
        input.minLength = 10;
        input.maxLength = 1024;
        label.append(span, input);
        form.insertBefore(label, document.getElementById("authError"));
        inputs.push(input);
      }
      const exit = document.createElement("button");
      exit.type = "button";
      exit.className = "secondary-button initial-password-exit";
      exit.textContent = text[5];
      exit.addEventListener("click", () => {
        void options.logout();
      });
      form.append(exit);
    }
    for (const [index, input] of inputs.entries()) {
      const span = input.parentElement?.querySelector("span");
      if (span) span.textContent = text[index + 2];
    }
    const exitButton = form.querySelector('button[type="button"]');
    if (exitButton) exitButton.textContent = text[5];
    const button = document.getElementById("authSubmit");
    if (button) {
      button.textContent = text[4];
      button.setAttribute("data-locale-skip", "");
    }
    const remember = form.querySelector(".auth-options");
    if (remember instanceof HTMLElement) remember.hidden = true;
    document.getElementById("authPassword")?.focus();
  }

  async function submit() {
    const text =
      copy[/** @type {keyof typeof copy} */ (options.locale())] ||
      copy["zh-CN"];
    const old = document.getElementById("authPassword");
    const otp = document.getElementById("authOtp");
    const error = document.getElementById("authError");
    if (!(old instanceof HTMLInputElement) || !error || inputs.length !== 2)
      return;
    try {
      if (inputs[0].value !== inputs[1].value) throw new Error("mismatch");
      const response = await options.fetch("/api/auth/change-password", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          old_password: old.value,
          new_password: inputs[0].value,
          otp: otp instanceof HTMLInputElement ? otp.value.trim() : "",
        }),
      });
      if (!response.ok) {
        const result = await response.json();
        throw new Error(result.error);
      }
      old.value = "";
      for (const input of inputs) input.value = "";
      await options.logout();
    } catch (failure) {
      const code = failure instanceof Error ? failure.message : "";
      error.textContent =
        text[
          code === "mismatch"
            ? 6
            : code === "password_unchanged"
              ? 7
              : code === "password_weak"
                ? 8
                : code === "password_length"
                  ? 9
                  : 10
        ];
      error.setAttribute("data-locale-skip", "");
      error.hidden = false;
    }
  }
  return { activate, submit };
}
