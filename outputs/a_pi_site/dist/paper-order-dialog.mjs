/** @param {string} symbol @param {string} side @param {unknown} value */
export function validatePaperOrder(symbol, side, value) {
  const code = symbol.trim().toUpperCase();
  const quantity = Number(value);
  if (!/^\d{6}(\.(SZ|SH|BJ))?$/.test(code))
    throw new Error("请输入有效的六位股票代码");
  if (!["BUY", "SELL"].includes(side)) throw new Error("请选择买入或卖出");
  if (!Number.isSafeInteger(quantity) || quantity <= 0)
    throw new Error("数量必须是正整数");
  const star = /^(688|689)\d{3}(\.SH)?$/.test(code);
  const beijing = /^(4|8|9)\d{5}(\.BJ)?$/.test(code);
  if (
    side === "BUY" &&
    ((star && quantity < 200) ||
      (beijing && quantity < 100) ||
      (!star && !beijing && quantity % 100 !== 0))
  )
    throw new Error(
      star
        ? "科创板买入至少 200 股"
        : beijing
          ? "北交所买入至少 100 股"
          : "沪深主板买入须为 100 股整数倍",
    );
  return { symbol: code, side, quantity };
}

/** @param {{translate:(text:string)=>string, submit:(order:{symbol:string,side:string,quantity:number},key:string)=>Promise<void>, allowed:()=>boolean}} options */
export function openPaperOrderDialog(options) {
  if (document.querySelector(".paper-order-dialog")) return;
  const opener = document.activeElement;
  const dialog = document.createElement("dialog");
  dialog.className =
    "account-security account-security-action paper-order-dialog";
  const title = document.createElement("h2");
  title.id = "paper-order-title";
  title.textContent = options.translate("新建纸面委托");
  dialog.setAttribute("aria-labelledby", title.id);
  const note = document.createElement("p");
  note.textContent = options.translate(
    "仅创建模拟委托，不向券商发送真实订单。",
  );
  const form = document.createElement("form");
  /** @param {string} id @param {string} text @param {HTMLInputElement|HTMLSelectElement} input */
  function field(id, text, input) {
    const label = document.createElement("label");
    label.htmlFor = id;
    label.textContent = options.translate(text);
    input.id = id;
    input.required = true;
    form.append(label, input);
  }
  const symbol = document.createElement("input");
  symbol.name = "symbol";
  symbol.placeholder = "000001.SZ";
  symbol.maxLength = 9;
  field("paper-order-symbol", "股票代码", symbol);
  const side = document.createElement("select");
  side.name = "side";
  for (const [value, text] of [
    ["", "请选择方向"],
    ["BUY", "买入"],
    ["SELL", "卖出"],
  ]) {
    const option = document.createElement("option");
    option.value = value;
    option.textContent = options.translate(text);
    side.append(option);
  }
  field("paper-order-side", "委托方向", side);
  const quantity = document.createElement("input");
  quantity.name = "quantity";
  quantity.type = "number";
  quantity.min = "1";
  quantity.step = "1";
  quantity.value = "100";
  field("paper-order-quantity", "数量（股）", quantity);
  const summary = document.createElement("p");
  summary.className = "paper-order-summary";
  summary.setAttribute("aria-live", "polite");
  form.addEventListener("input", () => {
    summary.textContent = options.translate(
      side.value === "BUY"
        ? "确认提交买入纸面委托"
        : side.value === "SELL"
          ? "确认提交卖出纸面委托"
          : "请选择方向",
    );
  });
  const error = document.createElement("p");
  error.className = "security-error";
  error.setAttribute("role", "alert");
  const actions = document.createElement("div");
  actions.className = "security-inline-actions";
  const cancel = document.createElement("button");
  cancel.type = "button";
  cancel.className = "secondary-button";
  cancel.textContent = options.translate("取消");
  const submit = document.createElement("button");
  submit.type = "submit";
  submit.className = "primary-button";
  submit.textContent = options.translate("确认提交纸面委托");
  let pending = false;
  // A stable key also covers a lost response followed by retry with unchanged fields.
  let lastPayload = "",
    key = "";
  cancel.addEventListener("click", () => {
    if (!pending) dialog.close();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (pending) return;
    try {
      if (!options.allowed())
        throw new Error("数据校验未通过，不能创建纸面委托");
      const order = validatePaperOrder(
        symbol.value,
        side.value,
        quantity.value,
      );
      const payload = JSON.stringify(order);
      if (payload !== lastPayload) {
        key = crypto.randomUUID();
        lastPayload = payload;
      }
      pending = true;
      submit.disabled = cancel.disabled = true;
      symbol.disabled = side.disabled = quantity.disabled = true;
      error.textContent = "";
      await options.submit(order, key);
      if (dialog.isConnected) dialog.close();
    } catch (caught) {
      error.textContent = options.translate(
        caught instanceof Error ? caught.message : String(caught),
      );
    } finally {
      pending = false;
      submit.disabled = cancel.disabled = false;
      symbol.disabled = side.disabled = quantity.disabled = false;
    }
  });
  dialog.addEventListener("cancel", (event) => {
    if (pending) event.preventDefault();
  });
  // While submitting, closing cannot imply that the already-sent request was cancelled.
  dialog.addEventListener("close", () => {
    dialog.remove();
    if (opener instanceof HTMLElement && opener.isConnected) opener.focus();
  });
  actions.append(cancel, submit);
  form.append(summary, error, actions);
  dialog.append(title, note, form);
  document.body.append(dialog);
  dialog.showModal();
  symbol.focus();
}
