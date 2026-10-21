/** @param {Document} root @param {(value:string)=>string} localeText */
export function installPasswordReveal(root, localeText) {
  const view = root.defaultView;
  if (!view) return;
  for (const button of root.querySelectorAll("[data-password-toggle]")) {
    const input = root.getElementById(button.dataset.passwordToggle || "");
    const showIcon = button.querySelector(".password-eye-show");
    const hideIcon = button.querySelector(".password-eye-hide");
    if (!(input instanceof view.HTMLInputElement) || !showIcon || !hideIcon)
      continue;
    const isMasked = input.type === "password";
    showIcon.toggleAttribute("hidden", isMasked);
    hideIcon.toggleAttribute("hidden", !isMasked);
    button.addEventListener("click", () => {
      const show = input.type === "password";
      const selection = [input.selectionStart, input.selectionEnd];
      input.type = show ? "text" : "password";
      button.setAttribute("aria-pressed", String(show));
      const label = localeText(show ? "隐藏密码" : "显示密码");
      button.setAttribute("aria-label", label);
      button.title = label;
      showIcon.toggleAttribute("hidden", !show);
      hideIcon.toggleAttribute("hidden", show);
      input.focus({ preventScroll: true });
      if (selection[0] !== null && selection[1] !== null)
        input.setSelectionRange(selection[0], selection[1]);
    });
  }
}
